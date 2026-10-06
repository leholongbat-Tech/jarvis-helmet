// Mask controller (FW2): two synchronized servos lifting the faceplate.
//
// - LEDC timer 1 (50 Hz, 14-bit) drives S0/S1 on channels 1/2. The very first duty is each
//   servo's stored resting angle, so the servos do not jump at boot.
// - One task ("mask_ctrl", internal-RAM stack because it writes NVS) owns the servos. API
//   calls put a small command into a mutex-protected mailbox and notify the task; they
//   never wait for motion. A newer command drops the pending commands it makes obsolete.
// - Every servo follows its own eased segment (cosine ease-in-out) sampled every 20 ms
//   against esp_timer time, so tick jitter never distorts the trajectory. A mask move
//   gives both servos the same end time; S1 starts MASK_S1_START_DELAY_MS later to spread
//   the inrush current. A new command starts from the current interpolated angles.
// - When the servos come to rest the position is stored in HelmetStore ("mask_pos"), and
//   so is each servo's own angle ("s0_pos" / "s1_pos").

#include "mask_controller.h"

#include <driver/ledc.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/task.h>

#include <array>
#include <cmath>
#include <cstdio>
#include <mutex>
#include <string>

#include "actuator_math.h"
#include "config.h"
#include "helmet_common.h"
#include "helmet_store.h"
#include "mcp_server.h"

// config.h (FW-A) defines these; the fallbacks carry the contract values.
#ifndef SERVO_LEDC_TIMER
#define SERVO_LEDC_TIMER LEDC_TIMER_1
#endif
#ifndef SERVO_S0_LEDC_CHANNEL
#define SERVO_S0_LEDC_CHANNEL LEDC_CHANNEL_1
#endif
#ifndef SERVO_S1_LEDC_CHANNEL
#define SERVO_S1_LEDC_CHANNEL LEDC_CHANNEL_2
#endif
#ifndef SERVO_LEDC_FREQ_HZ
#define SERVO_LEDC_FREQ_HZ 50
#endif
#ifndef SERVO_LEDC_RESOLUTION
#define SERVO_LEDC_RESOLUTION LEDC_TIMER_14_BIT
#endif
#ifndef SERVO_PULSE_MIN_US
#define SERVO_PULSE_MIN_US 500
#endif
#ifndef SERVO_PULSE_MAX_US
#define SERVO_PULSE_MAX_US 2400
#endif
#ifndef MASK_S1_START_DELAY_MS
#define MASK_S1_START_DELAY_MS 30
#endif
#ifndef MASK_DETACH_AFTER_MS
#define MASK_DETACH_AFTER_MS 0
#endif
// Calibration used until NVS holds one: 90 deg +- 10, S1 mounted mirrored.
#ifndef SERVO_S0_CLOSED_DEFAULT
#define SERVO_S0_CLOSED_DEFAULT 80
#endif
#ifndef SERVO_S0_OPEN_DEFAULT
#define SERVO_S0_OPEN_DEFAULT 100
#endif
#ifndef SERVO_S1_CLOSED_DEFAULT
#define SERVO_S1_CLOSED_DEFAULT 100
#endif
#ifndef SERVO_S1_OPEN_DEFAULT
#define SERVO_S1_OPEN_DEFAULT 80
#endif

namespace {

const char* const TAG = "MaskCtrl";

constexpr int kServoFreqHz = SERVO_LEDC_FREQ_HZ;
constexpr ledc_timer_bit_t kServoResolution = static_cast<ledc_timer_bit_t>(SERVO_LEDC_RESOLUTION);
// ledc_timer_bit_t values equal their bit count (LEDC_TIMER_14_BIT == 14).
constexpr int kServoResolutionBits = static_cast<int>(kServoResolution);
constexpr int kTickMs = 20;
constexpr int64_t kDetachAfterUs = static_cast<int64_t>(MASK_DETACH_AFTER_MS) * 1000;
// Raw calibration moves: a full 180 deg sweep takes about 1.1 s.
constexpr float kCalibrateMsPerDegree = 6.0f;
constexpr int kCalibrateMinMs = 150;
constexpr size_t kMailboxSize = 8;
constexpr uint32_t kTaskStackBytes = 4096;
constexpr UBaseType_t kTaskPriority = 5;
constexpr EventBits_t kIdleBit = BIT0;
constexpr EventBits_t kExitedBit = BIT1;

struct Command {
    enum class Type : uint8_t { kMask, kServo, kCalibrate, kStop };
    Type type = Type::kStop;
    int servo = 0;        // kServo, kCalibrate
    int position = 0;     // kMask: target 0..100
    int speed = -1;       // kMask: 10..100, -1 = stored default (resolved in Post)
    int angle = 0;        // kServo, kCalibrate: 0..180
    int duration_ms = 0;  // kServo: 0..5000
};

// One servo's eased move. A segment may start in the future (S1 stagger).
struct Segment {
    bool active = false;
    float from = 0.0f;
    float to = 0.0f;
    int64_t start_us = 0;
    int64_t duration_us = 0;

    float At(int64_t now_us) const {
        return actuator::EasedValue(from, to, static_cast<float>(now_us - start_us),
                                    static_cast<float>(duration_us));
    }
    bool DoneAt(int64_t now_us) const { return now_us >= start_us + duration_us; }
};

uint32_t AngleToDuty(float angle) {
    return actuator::PulseUsToDuty(
        actuator::AngleToPulseUs(angle, SERVO_PULSE_MIN_US, SERVO_PULSE_MAX_US),
        kServoResolutionBits, kServoFreqHz);
}

TickType_t UsToTicksCeil(int64_t us) {
    if (us <= 0) {
        return 0;
    }
    const int64_t tick_us = static_cast<int64_t>(portTICK_PERIOD_MS) * 1000;
    return static_cast<TickType_t>((us + tick_us - 1) / tick_us);
}

ServoCalibration LoadCalibration(HelmetStore& store, const char* closed_key, const char* open_key,
                                 int closed_default, int open_default) {
    ServoCalibration cal{closed_default, open_default};
    uint8_t closed = 0;
    uint8_t open = 0;
    if (store.GetU8(closed_key, closed) && store.GetU8(open_key, open)) {
        if (actuator::IsValidCalibration(closed, open)) {
            cal.closed_angle = closed;
            cal.open_angle = open;
        } else {
            ESP_LOGW(TAG, "Ignoring invalid calibration %s=%u %s=%u", closed_key,
                     static_cast<unsigned>(closed), open_key, static_cast<unsigned>(open));
        }
    }
    return cal;
}

}  // namespace

const char* MaskStateKindName(MaskState::Kind kind) {
    switch (kind) {
        case MaskState::Kind::kOpen:
            return "open";
        case MaskState::Kind::kMoving:
            return "moving";
        case MaskState::Kind::kClosed:
            break;
    }
    return "closed";
}

struct MaskController::Impl {
    const ledc_channel_t channel[helmet::kServoCount] = {
        static_cast<ledc_channel_t>(SERVO_S0_LEDC_CHANNEL),
        static_cast<ledc_channel_t>(SERVO_S1_LEDC_CHANNEL)};

    mutable std::mutex mutex;
    // Guarded by mutex.
    std::array<Command, kMailboxSize> mailbox{};
    size_t mailbox_count = 0;
    ServoCalibration cal[helmet::kServoCount];
    float angle[helmet::kServoCount] = {0.0f, 0.0f};  // last computed angle of each servo
    Segment segment[helmet::kServoCount];
    int target = 0;  // target position of the current (or last) motion
    int default_speed = helmet::kDefaultSpeed;
    bool motion_since_rest = false;  // servos moved since the last rest bookkeeping
    bool attached = true;            // PWM pulses on (false after detach)
    int64_t detach_at_us = 0;        // pending detach deadline, 0 = none
    bool quit = false;

    // Owned by the task.
    uint32_t written_duty[helmet::kServoCount] = {0, 0};
    int stored_position = -1;  // last mask_pos known to be in NVS, -1 = none
    int stored_angle[helmet::kServoCount] = {-1, -1};  // last s<N>_pos known to be in NVS
    bool duty_error_logged = false;

    EventGroupHandle_t events = nullptr;
    TaskHandle_t task = nullptr;

    bool Moving() const { return segment[0].active || segment[1].active; }
    bool Busy() const { return mailbox_count > 0 || Moving(); }

    // Mean position of both servos derived from their angles (they agree during mask moves).
    float CurrentPosition() const {
        float sum = 0.0f;
        for (int s = 0; s < helmet::kServoCount; ++s) {
            sum += actuator::AngleToPosition(angle[s], cal[s].closed_angle, cal[s].open_angle);
        }
        return sum / helmet::kServoCount;
    }

    // Position the servos will reach when their running segments end.
    float EndPosition() const {
        float sum = 0.0f;
        for (int s = 0; s < helmet::kServoCount; ++s) {
            const float a = segment[s].active ? segment[s].to : angle[s];
            sum += actuator::AngleToPosition(a, cal[s].closed_angle, cal[s].open_angle);
        }
        return sum / helmet::kServoCount;
    }

    bool PostMask(int percent, int speed) {
        Command cmd;
        cmd.type = Command::Type::kMask;
        cmd.position = helmet::ClampInt(percent, helmet::kPositionMin, helmet::kPositionMax);
        cmd.speed = speed < 0 ? -1 : helmet::ClampInt(speed, helmet::kSpeedMin, helmet::kSpeedMax);
        return Post(cmd);
    }

    bool Post(Command cmd) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (task == nullptr || quit) {
                return false;
            }
            DropObsolete(cmd);
            if (mailbox_count >= mailbox.size()) {
                return false;  // cannot happen: DropObsolete keeps at most 4 entries
            }
            if (cmd.type == Command::Type::kMask) {
                if (cmd.speed < 0) {
                    cmd.speed = default_speed;
                }
                target = cmd.position;
            }
            mailbox[mailbox_count++] = cmd;
            // Cleared here, under the same lock the task uses to set it, so WaitIdle()
            // right after a command never sees a stale idle bit.
            xEventGroupClearBits(events, kIdleBit);
        }
        xTaskNotifyGive(task);
        return true;
    }

    // Pending commands that the new one would immediately preempt are dropped: mask moves
    // and stops re-target both servos, servo moves only their own servo.
    void DropObsolete(const Command& cmd) {
        const bool both = cmd.type == Command::Type::kMask || cmd.type == Command::Type::kStop;
        size_t kept = 0;
        for (size_t i = 0; i < mailbox_count; ++i) {
            const Command old = mailbox[i];
            const bool same_servo =
                (old.type == Command::Type::kServo || old.type == Command::Type::kCalibrate) &&
                old.servo == cmd.servo;
            if (!both && !same_servo) {
                mailbox[kept++] = old;
            }
        }
        mailbox_count = kept;
    }

    // Task, under mutex.
    void Apply(const Command& cmd, int64_t now_us) {
        // Every command starts from where the servos are right now.
        for (int s = 0; s < helmet::kServoCount; ++s) {
            if (segment[s].active) {
                angle[s] = segment[s].At(now_us);
            }
        }
        switch (cmd.type) {
            case Command::Type::kStop:
                segment[0].active = false;
                segment[1].active = false;
                target = helmet::ClampInt(static_cast<int>(std::lround(CurrentPosition())),
                                          helmet::kPositionMin, helmet::kPositionMax);
                break;
            case Command::Type::kMask:
                StartMaskMove(cmd.position, cmd.speed, now_us);
                break;
            case Command::Type::kServo: {
                const ServoCalibration& c = cal[cmd.servo];
                const float to = actuator::ClampToCalibration(static_cast<float>(cmd.angle),
                                                              c.closed_angle, c.open_angle);
                StartServoMove(cmd.servo, to, cmd.duration_ms, now_us);
                break;
            }
            case Command::Type::kCalibrate: {
                // Raw angle without the calibrated clamp, so the limits can be found.
                const float to = static_cast<float>(cmd.angle);
                StartServoMove(cmd.servo, to,
                               actuator::TravelMs(angle[cmd.servo], to, kCalibrateMsPerDegree,
                                                  kCalibrateMinMs),
                               now_us);
                break;
            }
        }
    }

    void StartMaskMove(int position, int speed, int64_t now_us) {
        float to[helmet::kServoCount];
        int closed[helmet::kServoCount];
        int open[helmet::kServoCount];
        for (int s = 0; s < helmet::kServoCount; ++s) {
            const ServoCalibration& c = cal[s];
            closed[s] = c.closed_angle;
            open[s] = c.open_angle;
            to[s] = actuator::ClampToCalibration(
                actuator::PositionToAngle(static_cast<float>(position), c.closed_angle,
                                          c.open_angle),
                c.closed_angle, c.open_angle);
        }
        // Timed for the servo with the longest way, on its real angle: one left outside its
        // calibrated range by raw calibration eases back at this speed instead of snapping.
        // Inside the range this equals helmet::MaskMoveMs (contract §3.4).
        const int total_ms = actuator::MaskMoveDurationMs(
            angle, to, closed, open, helmet::kServoCount, helmet::MaskFullTravelMs(speed),
            helmet::kMaskMinMoveMs, helmet::MaskFullTravelMs(helmet::kSpeedMin));

        target = position;
        attached = true;
        detach_at_us = 0;
        motion_since_rest = true;
        if (total_ms <= 0) {
            // Already there within rounding: settle without a visible move.
            for (int s = 0; s < helmet::kServoCount; ++s) {
                angle[s] = to[s];
                segment[s].active = false;
            }
            return;
        }
        const int delay_ms = actuator::StaggerDelayMs(total_ms, MASK_S1_START_DELAY_MS);
        segment[0] = Segment{true, angle[0], to[0], now_us, static_cast<int64_t>(total_ms) * 1000};
        segment[1] = Segment{true, angle[1], to[1], now_us + static_cast<int64_t>(delay_ms) * 1000,
                             static_cast<int64_t>(total_ms - delay_ms) * 1000};
    }

    void StartServoMove(int servo, float to, int duration_ms, int64_t now_us) {
        attached = true;
        detach_at_us = 0;
        motion_since_rest = true;
        if (duration_ms <= 0) {
            angle[servo] = to;
            segment[servo].active = false;
        } else {
            segment[servo] =
                Segment{true, angle[servo], to, now_us, static_cast<int64_t>(duration_ms) * 1000};
        }
        target = helmet::ClampInt(static_cast<int>(std::lround(EndPosition())),
                                  helmet::kPositionMin, helmet::kPositionMax);
    }

    // Task, under mutex.
    void Advance(int64_t now_us) {
        for (int s = 0; s < helmet::kServoCount; ++s) {
            Segment& seg = segment[s];
            if (!seg.active) {
                continue;
            }
            if (seg.DoneAt(now_us)) {
                angle[s] = seg.to;
                seg.active = false;
            } else {
                angle[s] = seg.At(now_us);
            }
        }
    }

    // Task only (single writer of the servo channels).
    void WriteDuty(int servo, uint32_t duty) {
        if (duty == written_duty[servo]) {
            return;
        }
        esp_err_t err = ledc_set_duty(LEDC_LOW_SPEED_MODE, channel[servo], duty);
        if (err == ESP_OK) {
            err = ledc_update_duty(LEDC_LOW_SPEED_MODE, channel[servo]);
        }
        if (err == ESP_OK) {
            written_duty[servo] = duty;
        } else if (!duty_error_logged) {
            duty_error_logged = true;
            ESP_LOGE(TAG, "Servo %d duty update failed: %s", servo, esp_err_to_name(err));
        }
    }

    void Run() {
        TickType_t wait = portMAX_DELAY;
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (detach_at_us > 0) {
                wait = UsToTicksCeil(detach_at_us - esp_timer_get_time());
            }
        }
        for (;;) {
            ulTaskNotifyTake(pdTRUE, wait);
            const int64_t now_us = esp_timer_get_time();
            uint32_t duty[helmet::kServoCount] = {0, 0};
            bool write_duty = false;
            bool came_to_rest = false;
            int rest_position = 0;
            int rest_angle[helmet::kServoCount] = {0, 0};
            {
                std::lock_guard<std::mutex> lock(mutex);
                if (quit) {
                    break;
                }
                for (size_t i = 0; i < mailbox_count; ++i) {
                    Apply(mailbox[i], now_us);
                }
                mailbox_count = 0;
                Advance(now_us);

                if (!Moving()) {
                    xEventGroupSetBits(events, kIdleBit);
                    if (motion_since_rest) {
                        motion_since_rest = false;
                        came_to_rest = true;
                        rest_position =
                            helmet::ClampInt(static_cast<int>(std::lround(CurrentPosition())),
                                             helmet::kPositionMin, helmet::kPositionMax);
                        // Each servo's own angle too: after a move of one servo only (servo
                        // step, raw calibration) the mean position matches neither of them.
                        for (int s = 0; s < helmet::kServoCount; ++s) {
                            rest_angle[s] =
                                helmet::ClampInt(static_cast<int>(std::lround(angle[s])),
                                                 helmet::kAngleMin, helmet::kAngleMax);
                        }
                        if (kDetachAfterUs > 0) {
                            detach_at_us = now_us + kDetachAfterUs;
                        }
                    }
                    if (attached && detach_at_us > 0 && now_us >= detach_at_us) {
                        // No pulses: the servos stop holding (and stop buzzing).
                        attached = false;
                        detach_at_us = 0;
                        write_duty = true;
                    }
                }
                if (attached) {
                    for (int s = 0; s < helmet::kServoCount; ++s) {
                        duty[s] = AngleToDuty(angle[s]);
                    }
                    write_duty = true;
                }

                if (Moving()) {
                    wait = pdMS_TO_TICKS(kTickMs);
                } else if (attached && detach_at_us > 0) {
                    wait = UsToTicksCeil(detach_at_us - now_us);
                } else {
                    wait = portMAX_DELAY;
                }
            }

            if (write_duty) {
                for (int s = 0; s < helmet::kServoCount; ++s) {
                    WriteDuty(s, duty[s]);
                }
            }
            // Only once the servos rest, never during motion: the flash write stalls this
            // task for a few ms, which is harmless here. The angles go first: boot restores
            // them and falls back to mask_pos only for a servo without one.
            for (int s = 0; came_to_rest && s < helmet::kServoCount; ++s) {
                if (rest_angle[s] == stored_angle[s]) {
                    continue;
                }
                const char* key = s == 0 ? helmet_keys::kS0Pos : helmet_keys::kS1Pos;
                if (HelmetStore::GetInstance().SetU8(key, static_cast<uint8_t>(rest_angle[s]))) {
                    stored_angle[s] = rest_angle[s];
                } else {
                    ESP_LOGW(TAG, "Failed to store servo %d angle %d", s, rest_angle[s]);
                }
            }
            if (came_to_rest && rest_position != stored_position) {
                if (HelmetStore::GetInstance().SetU8(helmet_keys::kMaskPos,
                                                     static_cast<uint8_t>(rest_position))) {
                    stored_position = rest_position;
                } else {
                    ESP_LOGW(TAG, "Failed to store mask position %d", rest_position);
                }
            }
        }
        xEventGroupSetBits(events, kExitedBit);
        vTaskDelete(nullptr);
    }

    static void TaskEntry(void* arg) { static_cast<Impl*>(arg)->Run(); }
};

MaskController::MaskController(gpio_num_t s0_gpio, gpio_num_t s1_gpio) : impl_(new Impl()) {
    Impl& d = *impl_;
    auto& store = HelmetStore::GetInstance();
    if (!store.Init()) {
        ESP_LOGW(TAG, "Helmet store unavailable, using default calibration");
    }
    d.cal[0] = LoadCalibration(store, helmet_keys::kS0Closed, helmet_keys::kS0Open,
                               SERVO_S0_CLOSED_DEFAULT, SERVO_S0_OPEN_DEFAULT);
    d.cal[1] = LoadCalibration(store, helmet_keys::kS1Closed, helmet_keys::kS1Open,
                               SERVO_S1_CLOSED_DEFAULT, SERVO_S1_OPEN_DEFAULT);
    uint8_t value = 0;
    if (store.GetU8(helmet_keys::kMaskSpeed, value) && value >= helmet::kSpeedMin &&
        value <= helmet::kSpeedMax) {
        d.default_speed = value;
    }
    int position = helmet::kPositionMin;
    if (store.GetU8(helmet_keys::kMaskPos, value) && value <= helmet::kPositionMax) {
        position = value;
        d.stored_position = value;
    }
    // Each servo restarts at its own last resting angle (mask_pos only for a servo without
    // one), so neither moves at power-up even when the last move involved one servo only.
    for (int s = 0; s < helmet::kServoCount; ++s) {
        const ServoCalibration& c = d.cal[s];
        const char* key = s == 0 ? helmet_keys::kS0Pos : helmet_keys::kS1Pos;
        if (store.GetU8(key, value) && value <= helmet::kAngleMax) {
            d.stored_angle[s] = value;
        }
        d.angle[s] = actuator::BootAngle(d.stored_angle[s], static_cast<float>(position),
                                         c.closed_angle, c.open_angle);
    }
    d.target = helmet::ClampInt(static_cast<int>(std::lround(d.CurrentPosition())),
                                helmet::kPositionMin, helmet::kPositionMax);

    const ledc_timer_config_t timer_config = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = kServoResolution,
        .timer_num = static_cast<ledc_timer_t>(SERVO_LEDC_TIMER),
        .freq_hz = kServoFreqHz,
        .clk_cfg = LEDC_AUTO_CLK,
        .deconfigure = false,
    };
    esp_err_t err = ledc_timer_config(&timer_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Servo timer config failed: %s", esp_err_to_name(err));
    }
    const gpio_num_t gpio[helmet::kServoCount] = {s0_gpio, s1_gpio};
    for (int s = 0; s < helmet::kServoCount; ++s) {
        if (s > 0 && MASK_S1_START_DELAY_MS > 0) {
            // Like every mask move: if a servo has to correct (faceplate moved by hand while
            // off, first boot), the two inrush currents do not add up.
            vTaskDelay(pdMS_TO_TICKS(MASK_S1_START_DELAY_MS));
        }
        // The first pulse already matches the stored angle: no jump at boot.
        d.written_duty[s] = AngleToDuty(d.angle[s]);
        const ledc_channel_config_t channel_config = {
            .gpio_num = gpio[s],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = d.channel[s],
            .timer_sel = static_cast<ledc_timer_t>(SERVO_LEDC_TIMER),
            .duty = d.written_duty[s],
            .hpoint = 0,
        };
        err = ledc_channel_config(&channel_config);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Servo %d channel config failed: %s", s, esp_err_to_name(err));
        }
    }
    if (kDetachAfterUs > 0) {
        d.detach_at_us = esp_timer_get_time() + kDetachAfterUs;
    }

    d.events = xEventGroupCreate();
    if (d.events == nullptr) {
        ESP_LOGE(TAG, "Out of memory, mask controller disabled");
        return;
    }
    xEventGroupSetBits(d.events, kIdleBit);
    // xTaskCreate allocates the stack from internal RAM, which the NVS writes require.
    if (xTaskCreate(&Impl::TaskEntry, "mask_ctrl", kTaskStackBytes, impl_, kTaskPriority,
                    &d.task) != pdPASS) {
        d.task = nullptr;
        ESP_LOGE(TAG, "Failed to create the mask task");
        return;
    }
    ESP_LOGI(TAG,
             "Ready: position %d, S0 at %d deg (%d/%d closed/open), S1 at %d deg (%d/%d), "
             "speed %d",
             d.target, static_cast<int>(std::lround(d.angle[0])), d.cal[0].closed_angle,
             d.cal[0].open_angle, static_cast<int>(std::lround(d.angle[1])),
             d.cal[1].closed_angle, d.cal[1].open_angle, d.default_speed);
}

MaskController::~MaskController() {
    if (impl_->task != nullptr) {
        {
            std::lock_guard<std::mutex> lock(impl_->mutex);
            impl_->quit = true;
        }
        xTaskNotifyGive(impl_->task);
        const EventBits_t bits =
            xEventGroupWaitBits(impl_->events, kExitedBit, pdFALSE, pdTRUE, pdMS_TO_TICKS(2000));
        if ((bits & kExitedBit) == 0) {
            ESP_LOGE(TAG, "Mask task did not stop, leaking its state");
            return;
        }
    }
    for (int s = 0; s < helmet::kServoCount; ++s) {
        ledc_stop(LEDC_LOW_SPEED_MODE, impl_->channel[s], 0);
    }
    if (impl_->events != nullptr) {
        vEventGroupDelete(impl_->events);
    }
    delete impl_;
}

void MaskController::Open(int speed) { SetPosition(helmet::kPositionMax, speed); }

void MaskController::Close(int speed) { SetPosition(helmet::kPositionMin, speed); }

void MaskController::SetPosition(int percent, int speed) {
    if (!impl_->PostMask(percent, speed)) {
        ESP_LOGW(TAG, "Mask command dropped");
    }
}

void MaskController::MoveServo(int id, int angle, int duration_ms) {
    if (id < 0 || id >= helmet::kServoCount) {
        ESP_LOGW(TAG, "Invalid servo %d", id);
        return;
    }
    Command cmd;
    cmd.type = Command::Type::kServo;
    cmd.servo = id;
    cmd.angle = helmet::ClampInt(angle, helmet::kAngleMin, helmet::kAngleMax);
    cmd.duration_ms = helmet::ClampInt(duration_ms, 0, helmet::kServoDurationMaxMs);
    if (!impl_->Post(cmd)) {
        ESP_LOGW(TAG, "Servo command dropped");
    }
}

void MaskController::Stop() {
    Command cmd;
    cmd.type = Command::Type::kStop;
    impl_->Post(cmd);
}

bool MaskController::WaitIdle(int timeout_ms) {
    if (impl_->events == nullptr) {
        return true;
    }
    const TickType_t ticks = timeout_ms < 0 ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);
    const EventBits_t bits = xEventGroupWaitBits(impl_->events, kIdleBit, pdFALSE, pdTRUE, ticks);
    return (bits & kIdleBit) != 0;
}

bool MaskController::IsMoving() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->Busy();
}

MaskState MaskController::GetState() const {
    MaskState state;
    std::lock_guard<std::mutex> lock(impl_->mutex);
    state.position = helmet::ClampInt(static_cast<int>(std::lround(impl_->CurrentPosition())),
                                      helmet::kPositionMin, helmet::kPositionMax);
    state.target = impl_->target;
    if (impl_->Busy()) {
        state.kind = MaskState::Kind::kMoving;
    } else {
        state.kind = state.position == helmet::kPositionMin ? MaskState::Kind::kClosed
                                                            : MaskState::Kind::kOpen;
    }
    return state;
}

int MaskController::GetDefaultSpeed() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->default_speed;
}

bool MaskController::Calibrate(int servo, int angle) {
    if (servo < 0 || servo >= helmet::kServoCount || angle < helmet::kAngleMin ||
        angle > helmet::kAngleMax) {
        return false;
    }
    Command cmd;
    cmd.type = Command::Type::kCalibrate;
    cmd.servo = servo;
    cmd.angle = angle;
    return impl_->Post(cmd);
}

bool MaskController::SaveCalibration(int servo, int closed_angle, int open_angle) {
    if (servo < 0 || servo >= helmet::kServoCount ||
        !actuator::IsValidCalibration(closed_angle, open_angle)) {
        return false;
    }
    const char* closed_key = servo == 0 ? helmet_keys::kS0Closed : helmet_keys::kS1Closed;
    const char* open_key = servo == 0 ? helmet_keys::kS0Open : helmet_keys::kS1Open;
    const ServoCalibration previous = GetCalibration(servo);
    // Caller context: the MCP callbacks run on the main task, whose stack is internal RAM.
    auto& store = HelmetStore::GetInstance();
    if (!store.SetU8(closed_key, static_cast<uint8_t>(closed_angle))) {
        ESP_LOGE(TAG, "Failed to store %s", closed_key);
        return false;
    }
    if (!store.SetU8(open_key, static_cast<uint8_t>(open_angle))) {
        ESP_LOGE(TAG, "Failed to store %s", open_key);
        // Keep the stored pair consistent.
        store.SetU8(closed_key, static_cast<uint8_t>(previous.closed_angle));
        return false;
    }
    float held = 0.0f;
    float inside = 0.0f;
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        impl_->cal[servo] = ServoCalibration{closed_angle, open_angle};
        const Segment& seg = impl_->segment[servo];
        held = seg.active ? seg.to : impl_->angle[servo];
        inside = actuator::ClampToCalibration(held, closed_angle, open_angle);
    }
    ESP_LOGI(TAG, "Servo %d calibration saved: closed %d, open %d", servo, closed_angle,
             open_angle);
    if (std::fabs(inside - held) >= 0.5f) {
        // Left beyond the new limits by raw calibration (maybe straining against a stop):
        // ease it back into range at the calibration rate.
        Command cmd;
        cmd.type = Command::Type::kServo;
        cmd.servo = servo;
        cmd.angle = static_cast<int>(std::lround(inside));
        cmd.duration_ms = actuator::TravelMs(held, inside, kCalibrateMsPerDegree, kCalibrateMinMs);
        impl_->Post(cmd);
    }
    return true;
}

ServoCalibration MaskController::GetCalibration(int servo) const {
    if (servo < 0 || servo >= helmet::kServoCount) {
        return ServoCalibration{};
    }
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->cal[servo];
}

void MaskController::RegisterMcpTools() {
    auto& mcp = McpServer::GetInstance();

    mcp.AddTool("self.mask.open",
                "Mở mặt nạ (nâng tấm che mặt lên). / Open (raise) the helmet faceplate. "
                "Returns at once, the motion runs in the background.",
                PropertyList({Property("speed", kPropertyTypeInteger, helmet::kDefaultSpeed,
                                       helmet::kSpeedMin, helmet::kSpeedMax)}),
                [this](const PropertyList& properties) -> ToolResult {
                    if (!impl_->PostMask(helmet::kPositionMax, properties["speed"].value<int>())) {
                        return std::unexpected(helmet::SanitizeMessage("mask controller busy"));
                    }
                    return std::string("opening");
                });

    mcp.AddTool("self.mask.close",
                "Đóng mặt nạ (hạ tấm che mặt xuống). / Close (lower) the helmet faceplate. "
                "Returns at once, the motion runs in the background.",
                PropertyList({Property("speed", kPropertyTypeInteger, helmet::kDefaultSpeed,
                                       helmet::kSpeedMin, helmet::kSpeedMax)}),
                [this](const PropertyList& properties) -> ToolResult {
                    if (!impl_->PostMask(helmet::kPositionMin, properties["speed"].value<int>())) {
                        return std::unexpected(helmet::SanitizeMessage("mask controller busy"));
                    }
                    return std::string("closing");
                });

    mcp.AddTool("self.mask.set_position",
                "Mở mặt nạ một phần. / Move the faceplate to a partial position: "
                "percent 0 = closed, 100 = fully open.",
                PropertyList({Property("percent", kPropertyTypeInteger, helmet::kPositionMin,
                                       helmet::kPositionMax),
                              Property("speed", kPropertyTypeInteger, helmet::kDefaultSpeed,
                                       helmet::kSpeedMin, helmet::kSpeedMax)}),
                [this](const PropertyList& properties) -> ToolResult {
                    if (!impl_->PostMask(properties["percent"].value<int>(),
                                         properties["speed"].value<int>())) {
                        return std::unexpected(helmet::SanitizeMessage("mask controller busy"));
                    }
                    return std::string("moving");
                });

    mcp.AddTool("self.mask.get_state",
                "Xem trạng thái mặt nạ. / Get the faceplate state: open, closed or moving, "
                "with position 0-100 (0 = closed).",
                PropertyList(), [this](const PropertyList&) -> ToolResult {
                    const MaskState state = GetState();
                    char json[64];
                    snprintf(json, sizeof(json), "{\"state\":\"%s\",\"position\":%d}",
                             MaskStateKindName(state.kind), state.position);
                    return std::string(json);
                });

    // User-only: hidden from the LLM, reachable through the local MCP WebSocket (D7).
    mcp.AddUserOnlyTool(
        "self.servo.calibrate",
        "Hiệu chỉnh: quay một servo tới góc thô. / Calibration: drive one servo "
        "(0 = left, 1 = right) to a raw angle 0-180 without the calibrated limits.",
        PropertyList(
            {Property("servo", kPropertyTypeInteger, 0, helmet::kServoCount - 1),
             Property("angle", kPropertyTypeInteger, helmet::kAngleMin, helmet::kAngleMax)}),
        [this](const PropertyList& properties) -> ToolResult {
            if (!Calibrate(properties["servo"].value<int>(), properties["angle"].value<int>())) {
                return std::unexpected(helmet::SanitizeMessage("mask controller busy"));
            }
            return std::string("ok");
        });

    mcp.AddUserOnlyTool(
        "self.servo.save_calibration",
        "Lưu góc đóng/mở của một servo. / Save one servo's closed and open angles "
        "(0-180, must differ). Used for every later mask move.",
        PropertyList(
            {Property("servo", kPropertyTypeInteger, 0, helmet::kServoCount - 1),
             Property("closed_angle", kPropertyTypeInteger, helmet::kAngleMin, helmet::kAngleMax),
             Property("open_angle", kPropertyTypeInteger, helmet::kAngleMin, helmet::kAngleMax)}),
        [this](const PropertyList& properties) -> ToolResult {
            const int servo = properties["servo"].value<int>();
            const int closed_angle = properties["closed_angle"].value<int>();
            const int open_angle = properties["open_angle"].value<int>();
            if (closed_angle == open_angle) {
                return std::unexpected(
                    helmet::SanitizeMessage("closed_angle and open_angle must differ"));
            }
            if (!SaveCalibration(servo, closed_angle, open_angle)) {
                return std::unexpected(helmet::SanitizeMessage("failed to save calibration"));
            }
            return std::string("saved");
        });
}
