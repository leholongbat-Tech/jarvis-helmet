// Eye lights and spare output (FW3).
//
// - LEDC timer 2 (5 kHz, 10-bit): channel 3 drives the eyes (GPIO8), channel 4 the spare
//   output (GPIO9). Both start at duty 0 (the MOSFET gate pull-downs keep them off before).
// - Brightness is perceived percent, mapped to duty through gamma 2.2.
// - One task ("eyes_fx") owns both channels. API calls post commands to a mutex-protected
//   mailbox and notify the task, so every caller returns at once. A new eye command
//   cancels the running effect; the spare output never touches the eyes.
// - The S3 LEDC only fades linearly in duty (no hardware gamma curve), so ramps chain
//   20 ms hardware fades between gamma-corrected points: smooth at the PWM level and even
//   to the eye.
// - Optional assistant-state linkage (CONFIG_JARVIS_EYES_FOLLOW_STATE): listening -> 100 %,
//   speaking -> brightness follows the speaker level, otherwise the resting level. It only
//   modulates eyes that are on and idle, and an explicit command wins until the next
//   state change.

#include "output_controller.h"

#include <driver/ledc.h>
#include <esp_log.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <freertos/task.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <mutex>
#include <string>

#include "actuator_math.h"
#include "config.h"
#include "device_state.h"
#include "helmet_common.h"
#include "helmet_store.h"
#include "mcp_server.h"

// config.h (FW-A) defines these; the fallbacks carry the contract values.
#ifndef OUTPUT_LEDC_TIMER
#define OUTPUT_LEDC_TIMER LEDC_TIMER_2
#endif
#ifndef EYES_LEDC_CHANNEL
#define EYES_LEDC_CHANNEL LEDC_CHANNEL_3
#endif
#ifndef AUX_LEDC_CHANNEL
#define AUX_LEDC_CHANNEL LEDC_CHANNEL_4
#endif
#ifndef OUTPUT_LEDC_FREQ_HZ
#define OUTPUT_LEDC_FREQ_HZ 5000
#endif
#ifndef OUTPUT_LEDC_RESOLUTION
#define OUTPUT_LEDC_RESOLUTION LEDC_TIMER_10_BIT
#endif
#ifndef EYES_GAMMA
#define EYES_GAMMA 2.2f
#endif

namespace {

const char* const TAG = "OutputCtrl";

constexpr int kOutputFreqHz = OUTPUT_LEDC_FREQ_HZ;
constexpr ledc_timer_bit_t kOutputResolution =
    static_cast<ledc_timer_bit_t>(OUTPUT_LEDC_RESOLUTION);
// ledc_timer_bit_t values equal their bit count (LEDC_TIMER_10_BIT == 10).
constexpr int kOutputResolutionBits = static_cast<int>(kOutputResolution);
constexpr uint32_t kMaxDuty = actuator::MaxDuty(kOutputResolutionBits);  // 1024 = always on
constexpr float kGamma = EYES_GAMMA;
constexpr ledc_channel_t kEyesChannel = static_cast<ledc_channel_t>(EYES_LEDC_CHANNEL);
constexpr ledc_channel_t kAuxChannel = static_cast<ledc_channel_t>(AUX_LEDC_CHANNEL);

constexpr int kStepMs = 20;                // length of one chained hardware fade
constexpr int kSetRampMs = 150;            // SetEyes transition
constexpr int kBlinkMinMs = 150;           // double blink, whole effect
constexpr int kBlinkMaxMs = 600;           // blinks stay quick whatever duration_ms says
constexpr int kBlinkRestoreMs = 60;        // blink cancelled: back to the resting level
constexpr int kPowerUpFlickerMaxMs = 300;  // power_up: flicker part, at most 1/4 of it
constexpr int kPowerUpFlickerMinTotalMs = 240;
constexpr float kBreatheLowFraction = 0.1f;
constexpr int kFollowRampMs = 120;
constexpr int kSpeechStepMs = 40;
constexpr float kSpeechGain = 3.0f;    // speech RMS rarely exceeds ~0.3 of full scale
constexpr float kSpeechFloor = 0.35f;  // eyes never drop below 35 % of the base while speaking

constexpr size_t kMailboxSize = 16;
constexpr uint32_t kTaskStackBytes = 4096;
constexpr UBaseType_t kTaskPriority = 4;
constexpr EventBits_t kIdleBit = BIT0;
constexpr EventBits_t kExitedBit = BIT1;

enum class Effect : uint8_t { kNone, kBreathe, kBlink, kFlicker, kPowerUp, kFadeOut };

Effect EffectFromName(const std::string& name) {
    if (name == "breathe") {
        return Effect::kBreathe;
    }
    if (name == "blink") {
        return Effect::kBlink;
    }
    if (name == "flicker") {
        return Effect::kFlicker;
    }
    if (name == "power_up") {
        return Effect::kPowerUp;
    }
    if (name == "fade_out") {
        return Effect::kFadeOut;
    }
    return Effect::kNone;
}

const char* EffectName(Effect effect) {
    switch (effect) {
        case Effect::kBreathe:
            return "breathe";
        case Effect::kBlink:
            return "blink";
        case Effect::kFlicker:
            return "flicker";
        case Effect::kPowerUp:
            return "power_up";
        case Effect::kFadeOut:
            return "fade_out";
        case Effect::kNone:
            break;
    }
    return "";
}

// Finite effects are what WaitIdle() waits for; breathe repeats and counts as idle.
bool IsFinite(Effect effect) {
    return effect == Effect::kBlink || effect == Effect::kFlicker || effect == Effect::kPowerUp ||
           effect == Effect::kFadeOut;
}

// duration_ms 0 = the effect's default (D26, table in helmet_common.h).
int DefaultDurationMs(Effect effect) {
    switch (effect) {
        case Effect::kBreathe:
            return helmet::EyeEffectDefaultMs("breathe");
        case Effect::kFlicker:
            return helmet::EyeEffectDefaultMs("flicker");
        default:
            return helmet::kDefaultEffectDurationMs;
    }
}

struct Command {
    enum class Type : uint8_t { kSetEyes, kEffect, kStopEffect, kSetOutput, kDeviceState };
    Type type = Type::kStopEffect;
    bool on = false;     // kSetEyes, kSetOutput
    int brightness = 0;  // kSetEyes: resolved 0..100
    Effect effect = Effect::kNone;
    int duration_ms = 0;   // kEffect: resolved 100..10000
    int device_state = 0;  // kDeviceState
    bool user = true;      // explicit command: suspends the state linkage
};

bool IsEyeCommand(Command::Type type) {
    return type == Command::Type::kSetEyes || type == Command::Type::kEffect ||
           type == Command::Type::kStopEffect;
}

// What the eye channel is doing.
enum class Anim : uint8_t { kSteady, kRamp, kBreathe, kBlink, kFlicker, kPowerUp, kSpeech };

struct Animation {
    Anim kind = Anim::kSteady;
    Effect effect = Effect::kNone;  // user-visible effect, kNone for plain ramps
    int64_t start_us = 0;
    int64_t end_us = 0;    // end of finite animations
    int64_t next_us = 0;   // next step
    int64_t split_us = 0;  // power_up: end of the flicker part
    float from = 0.0f;     // ramp start; blink: resting level
    float to = 0.0f;       // ramp end; blink: blinked level
    float hi = 0.0f;       // breathe peak, flicker/power_up/speech base
    float lo = 0.0f;       // breathe floor
    int period_ms = 0;     // breathe
    int phase = 0;         // blink key / flicker on-off counter
};

// Double blink: four fades at fixed fractions of the blink time.
struct BlinkKey {
    float at;
    float fade;
    bool blinked;
};
constexpr BlinkKey kBlinkKeys[] = {
    {0.00f, 0.12f, true}, {0.25f, 0.15f, false}, {0.50f, 0.12f, true}, {0.75f, 0.15f, false}};
constexpr int kBlinkKeyCount = sizeof(kBlinkKeys) / sizeof(kBlinkKeys[0]);

float RandomUnit() { return static_cast<float>(esp_random() >> 8) / 16777216.0f; }

float RandomRange(float lo, float hi) { return lo + (hi - lo) * RandomUnit(); }

int RandomInt(int lo, int hi) {
    return lo + static_cast<int>(esp_random() % static_cast<uint32_t>(hi - lo + 1));
}

TickType_t UsToTicksCeil(int64_t us) {
    if (us <= 0) {
        return 0;
    }
    const int64_t tick_us = static_cast<int64_t>(portTICK_PERIOD_MS) * 1000;
    return static_cast<TickType_t>((us + tick_us - 1) / tick_us);
}

int64_t MsToUs(int ms) { return static_cast<int64_t>(ms) * 1000; }

}  // namespace

struct OutputController::Impl {
    int default_brightness = helmet::kDefaultBrightness;  // constant after construction
    bool fade_ready = false;                              // LEDC fade service usable

    mutable std::mutex mutex;
    // Guarded by mutex.
    std::array<Command, kMailboxSize> mailbox{};
    size_t mailbox_count = 0;
    OutputState state;  // published snapshot
    int last_posted_state = -1;
    bool quit = false;

    std::atomic<float> speech_level{0.0f};

    // Owned by the task.
    float level = 0.0f;  // level the eyes reach at the end of the fade in flight
    Animation anim;
    bool steady_on = false;  // resting state reported by GetState()
    int steady_level = 0;
    bool output_on = false;
    enum class Follow : uint8_t { kNone, kListening, kSpeaking };
    Follow follow = Follow::kNone;
    int device_state = kDeviceStateUnknown;
    bool follow_suppressed = false;
    bool ledc_error_logged = false;

    EventGroupHandle_t events = nullptr;
    TaskHandle_t task = nullptr;

    // ---- API side (any task) -----------------------------------------------------------

    bool PostSetEyes(bool on, int brightness, bool user) {
        Command cmd;
        cmd.type = Command::Type::kSetEyes;
        cmd.on = on;
        cmd.brightness = brightness < 0 ? default_brightness
                                        : helmet::ClampInt(brightness, helmet::kBrightnessMin,
                                                           helmet::kBrightnessMax);
        cmd.user = user;
        return Post(cmd);
    }

    bool Post(const Command& cmd) {
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (task == nullptr || quit) {
                return false;
            }
            if (cmd.type == Command::Type::kDeviceState) {
                // VAD updates repeat the same state; only real changes matter.
                if (cmd.device_state == last_posted_state) {
                    return true;
                }
                last_posted_state = cmd.device_state;
            }
            DropObsolete(cmd);
            if (mailbox_count >= mailbox.size()) {
                return false;
            }
            mailbox[mailbox_count++] = cmd;
            PublishIntentLocked(cmd);
            // Cleared under the lock the task uses to set it: no stale idle for WaitIdle().
            xEventGroupClearBits(events, kIdleBit);
        }
        xTaskNotifyGive(task);
        return true;
    }

    // SetEyes fully defines the eye state, so pending eye commands before it are moot;
    // the newest output and device-state commands replace older ones.
    void DropObsolete(const Command& cmd) {
        size_t kept = 0;
        for (size_t i = 0; i < mailbox_count; ++i) {
            const Command old = mailbox[i];
            bool obsolete = false;
            if (cmd.type == Command::Type::kSetEyes) {
                obsolete = IsEyeCommand(old.type);
            } else if (cmd.type == Command::Type::kSetOutput ||
                       cmd.type == Command::Type::kDeviceState) {
                obsolete = old.type == cmd.type;
            }
            if (!obsolete) {
                mailbox[kept++] = old;
            }
        }
        mailbox_count = kept;
    }

    // Reflect the command in GetState() right away; the task confirms it shortly after.
    void PublishIntentLocked(const Command& cmd) {
        switch (cmd.type) {
            case Command::Type::kSetEyes:
                state.eyes_on = cmd.on && cmd.brightness > 0;
                state.eyes_brightness = state.eyes_on ? cmd.brightness : 0;
                state.eyes_effect.clear();
                break;
            case Command::Type::kEffect:
                state.eyes_effect = EffectName(cmd.effect);
                break;
            case Command::Type::kSetOutput:
                state.output_on = cmd.on;
                break;
            case Command::Type::kStopEffect:
            case Command::Type::kDeviceState:
                break;
        }
    }

    // ---- LEDC (task only) -------------------------------------------------------------

    void LogLedcError(esp_err_t err) {
        if (err != ESP_OK && !ledc_error_logged) {
            ledc_error_logged = true;
            ESP_LOGE(TAG, "LEDC update failed: %s", esp_err_to_name(err));
        }
    }

    void SetDutyNow(ledc_channel_t channel, uint32_t duty) {
        esp_err_t err;
        if (fade_ready) {
            // A running fade would block the duty update until it ends.
            ledc_fade_stop(LEDC_LOW_SPEED_MODE, channel);
            err = ledc_set_duty_and_update(LEDC_LOW_SPEED_MODE, channel, duty, 0);
        } else {
            err = ledc_set_duty(LEDC_LOW_SPEED_MODE, channel, duty);
            if (err == ESP_OK) {
                err = ledc_update_duty(LEDC_LOW_SPEED_MODE, channel);
            }
        }
        LogLedcError(err);
    }

    // Moves the eyes to a perceived level, as a hardware fade over `ms` (0 = at once).
    void FadeEyesTo(float target, int ms) {
        level = std::clamp(target, 0.0f, 100.0f);
        const uint32_t duty = actuator::GammaDuty(level, kMaxDuty, kGamma);
        if (!fade_ready || ms <= 0) {
            SetDutyNow(kEyesChannel, duty);
            return;
        }
        ledc_fade_stop(LEDC_LOW_SPEED_MODE, kEyesChannel);
        const esp_err_t err = ledc_set_fade_time_and_start(
            LEDC_LOW_SPEED_MODE, kEyesChannel, duty, static_cast<uint32_t>(ms), LEDC_FADE_NO_WAIT);
        if (err != ESP_OK) {
            LogLedcError(err);
            SetDutyNow(kEyesChannel, duty);
        }
    }

    // Perceived level the eyes show right now; freezes a fade in flight so the next ramp
    // continues from exactly there.
    float LiveLevel() {
        if (!fade_ready) {
            return level;
        }
        ledc_fade_stop(LEDC_LOW_SPEED_MODE, kEyesChannel);
        level = actuator::DutyToBrightness(ledc_get_duty(LEDC_LOW_SPEED_MODE, kEyesChannel),
                                           kMaxDuty, kGamma);
        return level;
    }

    // ---- Animations (task only) -------------------------------------------------------

    void StartRamp(float to, int duration_ms, Effect effect) {
        const int64_t now = esp_timer_get_time();
        Animation a;
        a.kind = Anim::kRamp;
        a.effect = effect;
        a.from = LiveLevel();
        a.to = to;
        a.start_us = now;
        a.end_us = now + MsToUs(duration_ms);
        a.next_us = now;
        anim = a;
    }

    void StartSpeech() {
        Animation a;
        a.kind = Anim::kSpeech;
        a.hi = static_cast<float>(steady_level);
        a.next_us = esp_timer_get_time();
        anim = a;
    }

    void StartEffect(Effect effect, int duration_ms) {
        const int64_t now = esp_timer_get_time();
        const float resting = steady_on ? static_cast<float>(steady_level) : 0.0f;
        const float base = static_cast<float>(default_brightness);
        Animation a;
        a.effect = effect;
        a.start_us = now;
        a.next_us = now;
        a.end_us = now + MsToUs(duration_ms);
        switch (effect) {
            case Effect::kBreathe: {
                a.kind = Anim::kBreathe;
                a.hi = steady_on ? resting : base;
                a.lo = a.hi * kBreatheLowFraction;
                a.period_ms = duration_ms;
                // Enter the curve on the side nearest the live level: no visible jump.
                if (LiveLevel() >= (a.lo + a.hi) / 2.0f) {
                    a.start_us = now - MsToUs(duration_ms) / 2;
                }
                steady_on = true;
                steady_level = static_cast<int>(std::lround(a.hi));
                break;
            }
            case Effect::kBlink:
                a.kind = Anim::kBlink;
                a.end_us = now + MsToUs(helmet::ClampInt(duration_ms, kBlinkMinMs, kBlinkMaxMs));
                a.from = resting;
                a.to = resting > 0.0f ? 0.0f : base;  // dark eyes blink on, lit eyes blink off
                break;
            case Effect::kFlicker:
                a.kind = Anim::kFlicker;
                a.hi = base;
                steady_on = true;
                steady_level = default_brightness;
                break;
            case Effect::kPowerUp: {
                a.kind = Anim::kPowerUp;
                a.hi = base;
                const int flicker_ms = duration_ms >= kPowerUpFlickerMinTotalMs
                                           ? std::min(kPowerUpFlickerMaxMs, duration_ms / 4)
                                           : 0;
                a.split_us = now + MsToUs(flicker_ms);
                steady_on = true;
                steady_level = default_brightness;
                break;
            }
            case Effect::kFadeOut:
                a.kind = Anim::kRamp;
                a.from = LiveLevel();
                a.to = 0.0f;
                break;
            case Effect::kNone:
                return;
        }
        anim = a;
    }

    // Cancels a user effect. A blink goes back to the resting level; anything else keeps
    // the brightness it shows right now.
    void StopEffect() {
        if (anim.effect == Effect::kNone) {
            return;
        }
        if (anim.effect == Effect::kBlink) {
            const float resting = anim.from;
            anim = Animation{};
            FadeEyesTo(resting, kBlinkRestoreMs);
            return;
        }
        const float live = LiveLevel();
        anim = Animation{};
        steady_on = live > 0.0f;
        steady_level =
            steady_on ? helmet::ClampInt(static_cast<int>(std::lround(live)), 1, 100) : 0;
    }

    // End of a finite animation.
    void Finish() {
        const Effect effect = anim.effect;
        anim = Animation{};
        switch (effect) {
            case Effect::kFlicker:
            case Effect::kPowerUp:
                FadeEyesTo(static_cast<float>(default_brightness), 0);
                break;
            case Effect::kFadeOut:
                steady_on = false;
                steady_level = 0;
                break;
            default:
                break;
        }
    }

    // One hardware fade toward the ramp value kStepMs ahead. Returns true once ended.
    bool StepRamp(int64_t now, float from, float to, int64_t start_us, int64_t end_us) {
        if (now >= end_us) {
            FadeEyesTo(to, 0);  // settle exactly
            return true;
        }
        const int64_t t1 = std::min(now + MsToUs(kStepMs), end_us);
        const float value = actuator::RampLevel(from, to, static_cast<float>(t1 - start_us),
                                                static_cast<float>(end_us - start_us));
        FadeEyesTo(value, static_cast<int>((t1 - now + 500) / 1000));
        anim.next_us = t1;
        return false;
    }

    // Random on/off segments until `until_us`; `dim` is the weaker power_up variant.
    void StepFlicker(int64_t now, int64_t until_us, float base, bool dim) {
        const bool lit = (anim.phase++ % 2) == 0;
        float value;
        int hold_ms;
        if (lit) {
            value = base * (dim ? RandomRange(0.10f, 0.45f) : RandomRange(0.45f, 1.0f));
            hold_ms = dim ? RandomInt(20, 70) : RandomInt(30, 110);
        } else {
            value = dim ? 0.0f : base * RandomRange(0.0f, 0.12f);
            hold_ms = RandomInt(20, dim ? 60 : 70);
        }
        FadeEyesTo(value, 0);
        anim.next_us = std::min(now + MsToUs(hold_ms), until_us);
    }

    void StepBlink(int64_t now) {
        const float total_ms = static_cast<float>(anim.end_us - anim.start_us) / 1000.0f;
        if (anim.phase >= kBlinkKeyCount) {
            if (now >= anim.end_us) {
                FadeEyesTo(anim.from, 0);
                Finish();
            } else {
                anim.next_us = anim.end_us;
            }
            return;
        }
        const BlinkKey& key = kBlinkKeys[anim.phase];
        FadeEyesTo(key.blinked ? anim.to : anim.from, static_cast<int>(key.fade * total_ms));
        ++anim.phase;
        anim.next_us = anim.phase < kBlinkKeyCount
                           ? anim.start_us + static_cast<int64_t>(kBlinkKeys[anim.phase].at *
                                                                  total_ms * 1000.0f)
                           : anim.end_us;
    }

    void Step(int64_t now) {
        if (anim.kind == Anim::kSteady || now < anim.next_us) {
            return;
        }
        switch (anim.kind) {
            case Anim::kRamp:
                if (StepRamp(now, anim.from, anim.to, anim.start_us, anim.end_us)) {
                    Finish();
                }
                break;
            case Anim::kBreathe: {
                const int64_t t1 = now + MsToUs(kStepMs);
                // Phase from integer time, so hours of breathing keep full precision.
                const int64_t phase_ms = ((t1 - anim.start_us) / 1000) % anim.period_ms;
                FadeEyesTo(
                    actuator::BreatheLevel(static_cast<float>(phase_ms),
                                           static_cast<float>(anim.period_ms), anim.lo, anim.hi),
                    kStepMs);
                anim.next_us = t1;
                break;
            }
            case Anim::kBlink:
                StepBlink(now);
                break;
            case Anim::kFlicker:
                if (now >= anim.end_us) {
                    Finish();
                } else {
                    StepFlicker(now, anim.end_us, anim.hi, false);
                }
                break;
            case Anim::kPowerUp:
                if (now < anim.split_us) {
                    StepFlicker(now, anim.split_us, anim.hi, true);
                } else if (StepRamp(now, 0.0f, anim.hi, anim.split_us, anim.end_us)) {
                    Finish();
                }
                break;
            case Anim::kSpeech:
                FadeEyesTo(actuator::SpeechBrightness(anim.hi,
                                                      speech_level.load(std::memory_order_relaxed),
                                                      kSpeechGain, kSpeechFloor),
                           kSpeechStepMs);
                anim.next_us = now + MsToUs(kSpeechStepMs);
                break;
            case Anim::kSteady:
                break;
        }
    }

    // ---- Assistant-state linkage (task only) ------------------------------------------

    void SuspendFollow() {
        follow_suppressed = true;
        follow = Follow::kNone;
    }

    void OnDeviceState(int new_state) {
        if (new_state == device_state) {
            return;
        }
        device_state = new_state;
        follow_suppressed = false;  // an explicit command only wins until the next change
        // Never light eyes the user switched off and never cut into an effect.
        const bool can_modulate = steady_on && anim.effect == Effect::kNone;
        if (device_state == kDeviceStateListening && can_modulate) {
            follow = Follow::kListening;
            StartRamp(static_cast<float>(helmet::kBrightnessMax), kFollowRampMs, Effect::kNone);
        } else if (device_state == kDeviceStateSpeaking && can_modulate) {
            follow = Follow::kSpeaking;
            StartSpeech();
        } else if (follow != Follow::kNone) {
            follow = Follow::kNone;
            if (anim.effect == Effect::kNone) {
                StartRamp(steady_on ? static_cast<float>(steady_level) : 0.0f, kFollowRampMs * 2,
                          Effect::kNone);
            }
        }
    }

    // ---- Task -------------------------------------------------------------------------

    void Handle(const Command& cmd) {
        switch (cmd.type) {
            case Command::Type::kSetEyes:
                if (cmd.user) {
                    SuspendFollow();
                }
                steady_on = cmd.on && cmd.brightness > 0;
                steady_level = steady_on ? cmd.brightness : 0;
                StartRamp(static_cast<float>(steady_level), kSetRampMs, Effect::kNone);
                break;
            case Command::Type::kEffect:
                if (cmd.user) {
                    SuspendFollow();
                }
                StartEffect(cmd.effect, cmd.duration_ms);
                break;
            case Command::Type::kStopEffect:
                StopEffect();
                break;
            case Command::Type::kSetOutput:
                output_on = cmd.on;
                SetDutyNow(kAuxChannel, cmd.on ? kMaxDuty : 0);
                break;
            case Command::Type::kDeviceState:
                OnDeviceState(cmd.device_state);
                break;
        }
    }

    // Under mutex. Skipped while commands are pending: they publish their own intent.
    void PublishLocked() {
        if (mailbox_count > 0) {
            return;
        }
        state.eyes_on = steady_on;
        state.eyes_brightness = steady_on ? steady_level : 0;
        state.eyes_effect = EffectName(anim.effect);
        state.output_on = output_on;
    }

    void Run() {
        std::array<Command, kMailboxSize> commands;
        for (;;) {
            const TickType_t wait = anim.kind == Anim::kSteady
                                        ? portMAX_DELAY
                                        : UsToTicksCeil(anim.next_us - esp_timer_get_time());
            ulTaskNotifyTake(pdTRUE, wait);
            size_t count = 0;
            {
                std::lock_guard<std::mutex> lock(mutex);
                if (quit) {
                    break;
                }
                count = mailbox_count;
                std::copy_n(mailbox.begin(), count, commands.begin());
                mailbox_count = 0;
            }
            for (size_t i = 0; i < count; ++i) {
                Handle(commands[i]);
            }
            Step(esp_timer_get_time());
            {
                std::lock_guard<std::mutex> lock(mutex);
                PublishLocked();
                if (mailbox_count == 0 && !IsFinite(anim.effect)) {
                    xEventGroupSetBits(events, kIdleBit);
                }
            }
        }
        xEventGroupSetBits(events, kExitedBit);
        vTaskDelete(nullptr);
    }

    static void TaskEntry(void* arg) { static_cast<Impl*>(arg)->Run(); }
};

OutputController::OutputController(gpio_num_t eyes_gpio, gpio_num_t output_gpio)
    : impl_(new Impl()) {
    Impl& d = *impl_;
    auto& store = HelmetStore::GetInstance();
    if (!store.Init()) {
        ESP_LOGW(TAG, "Helmet store unavailable, using default brightness");
    }
    uint8_t value = 0;
    if (store.GetU8(helmet_keys::kEyesBright, value) && value >= 1 &&
        value <= helmet::kBrightnessMax) {
        d.default_brightness = value;
    }

    const ledc_timer_config_t timer_config = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = kOutputResolution,
        .timer_num = static_cast<ledc_timer_t>(OUTPUT_LEDC_TIMER),
        .freq_hz = kOutputFreqHz,
        .clk_cfg = LEDC_AUTO_CLK,
        .deconfigure = false,
    };
    esp_err_t err = ledc_timer_config(&timer_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Output timer config failed: %s", esp_err_to_name(err));
    }
    const gpio_num_t gpio[2] = {eyes_gpio, output_gpio};
    const ledc_channel_t channel[2] = {kEyesChannel, kAuxChannel};
    for (int i = 0; i < 2; ++i) {
        const ledc_channel_config_t channel_config = {
            .gpio_num = gpio[i],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = channel[i],
            .timer_sel = static_cast<ledc_timer_t>(OUTPUT_LEDC_TIMER),
            .duty = 0,  // off at boot
            .hpoint = 0,
        };
        err = ledc_channel_config(&channel_config);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Output channel %d config failed: %s", static_cast<int>(channel[i]),
                     esp_err_to_name(err));
        }
    }
    // Installs the LEDC fade ISR once; INVALID_STATE means it is already installed.
    err = ledc_fade_func_install(0);
    d.fade_ready = err == ESP_OK || err == ESP_ERR_INVALID_STATE;
    if (!d.fade_ready) {
        ESP_LOGW(TAG, "LEDC fade service unavailable (%s), ramps are stepped",
                 esp_err_to_name(err));
    }

    d.events = xEventGroupCreate();
    if (d.events == nullptr) {
        ESP_LOGE(TAG, "Out of memory, output controller disabled");
        return;
    }
    xEventGroupSetBits(d.events, kIdleBit);
    if (xTaskCreate(&Impl::TaskEntry, "eyes_fx", kTaskStackBytes, impl_, kTaskPriority, &d.task) !=
        pdPASS) {
        d.task = nullptr;
        ESP_LOGE(TAG, "Failed to create the effects task");
        return;
    }
#ifdef CONFIG_JARVIS_EYES_ON_AT_BOOT
    d.PostSetEyes(true, -1, false);
#endif
    ESP_LOGI(TAG, "Ready: default brightness %d", d.default_brightness);
}

OutputController::~OutputController() {
    if (impl_->task != nullptr) {
        {
            std::lock_guard<std::mutex> lock(impl_->mutex);
            impl_->quit = true;
        }
        xTaskNotifyGive(impl_->task);
        const EventBits_t bits =
            xEventGroupWaitBits(impl_->events, kExitedBit, pdFALSE, pdTRUE, pdMS_TO_TICKS(2000));
        if ((bits & kExitedBit) == 0) {
            ESP_LOGE(TAG, "Effects task did not stop, leaking its state");
            return;
        }
    }
    if (impl_->fade_ready) {
        ledc_fade_stop(LEDC_LOW_SPEED_MODE, kEyesChannel);
    }
    ledc_stop(LEDC_LOW_SPEED_MODE, kEyesChannel, 0);
    ledc_stop(LEDC_LOW_SPEED_MODE, kAuxChannel, 0);
    if (impl_->events != nullptr) {
        vEventGroupDelete(impl_->events);
    }
    delete impl_;
}

void OutputController::SetEyes(bool on, int brightness) {
    if (!impl_->PostSetEyes(on, brightness, true)) {
        ESP_LOGW(TAG, "Eyes command dropped");
    }
}

bool OutputController::EyesEffect(const std::string& name, int duration_ms) {
    const Effect effect = EffectFromName(name);
    if (effect == Effect::kNone) {
        return false;
    }
    Command cmd;
    cmd.type = Command::Type::kEffect;
    cmd.effect = effect;
    cmd.duration_ms = duration_ms <= 0 ? DefaultDurationMs(effect)
                                       : helmet::ClampInt(duration_ms, helmet::kEffectDurationMinMs,
                                                          helmet::kEffectDurationMaxMs);
    if (!impl_->Post(cmd)) {
        ESP_LOGW(TAG, "Effect %s dropped", name.c_str());
        return false;
    }
    return true;
}

void OutputController::StopEffect() {
    Command cmd;
    cmd.type = Command::Type::kStopEffect;
    impl_->Post(cmd);
}

void OutputController::SetOutput(bool on) {
    Command cmd;
    cmd.type = Command::Type::kSetOutput;
    cmd.on = on;
    if (!impl_->Post(cmd)) {
        ESP_LOGW(TAG, "Output command dropped");
    }
}

bool OutputController::WaitIdle(int timeout_ms) {
    if (impl_->events == nullptr) {
        return true;
    }
    const TickType_t ticks = timeout_ms < 0 ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);
    const EventBits_t bits = xEventGroupWaitBits(impl_->events, kIdleBit, pdFALSE, pdTRUE, ticks);
    return (bits & kIdleBit) != 0;
}

OutputState OutputController::GetState() const {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    return impl_->state;
}

int OutputController::GetDefaultBrightness() const { return impl_->default_brightness; }

void OutputController::OnDeviceStateChanged(int device_state) {
#ifdef CONFIG_JARVIS_EYES_FOLLOW_STATE
    Command cmd;
    cmd.type = Command::Type::kDeviceState;
    cmd.device_state = device_state;
    cmd.user = false;
    impl_->Post(cmd);
#else
    (void)device_state;
#endif
}

void OutputController::SetSpeechLevel(float level) {
#ifdef CONFIG_JARVIS_EYES_FOLLOW_STATE
    impl_->speech_level.store(std::clamp(level, 0.0f, 1.0f), std::memory_order_relaxed);
#else
    (void)level;
#endif
}

void OutputController::RegisterMcpTools() {
    auto& mcp = McpServer::GetInstance();

    mcp.AddTool(
        "self.eyes.set",
        "Bật/tắt đèn mắt, chỉnh độ sáng. / Turn the eye lights on or off and set their "
        "brightness 0-100.",
        PropertyList({Property("on", kPropertyTypeBoolean),
                      Property("brightness", kPropertyTypeInteger, helmet::kDefaultBrightness,
                               helmet::kBrightnessMin, helmet::kBrightnessMax)}),
        [this](const PropertyList& properties) -> ToolResult {
            if (!impl_->PostSetEyes(properties["on"].value<bool>(),
                                    properties["brightness"].value<int>(), true)) {
                return std::unexpected(helmet::SanitizeMessage("eye lights busy"));
            }
            return std::string("ok");
        });

    mcp.AddTool("self.eyes.effect",
                "Hiệu ứng đèn mắt. / Eye light effect: breathe (repeats until another command, "
                "duration_ms = cycle), blink, flicker, power_up (light up), fade_out (dim to off). "
                "duration_ms 0 = default (breathe 2500, flicker 600, others 1000).",
                PropertyList(
                    {Property("name", kPropertyTypeString).SetMaxLength(16),
                     Property("duration_ms", kPropertyTypeInteger, 0, 0,
                              helmet::kEffectDurationMaxMs)}),
                [this](const PropertyList& properties) -> ToolResult {
                    const std::string& name = properties["name"].value<std::string>();
                    if (!helmet::IsEyeEffect(name)) {
                        return std::unexpected(helmet::SanitizeMessage(
                            "unknown effect " + name +
                            ", use breathe, blink, flicker, power_up or fade_out"));
                    }
                    const int duration_ms = properties["duration_ms"].value<int>();
                    if (!helmet::IsValidEffectDuration(duration_ms)) {
                        return std::unexpected(helmet::SanitizeMessage(
                            "duration_ms must be 0 (effect default) or " +
                            std::to_string(helmet::kEffectDurationMinMs) + ".." +
                            std::to_string(helmet::kEffectDurationMaxMs)));
                    }
                    if (!EyesEffect(name, duration_ms)) {
                        return std::unexpected(helmet::SanitizeMessage("eye lights busy"));
                    }
                    return std::string("ok");
                });

    mcp.AddTool("self.output.set",
                "Bật/tắt ngõ ra phụ (kênh dự phòng). / Switch the auxiliary spare output on or "
                "off.",
                PropertyList({Property("on", kPropertyTypeBoolean)}),
                [this](const PropertyList& properties) -> ToolResult {
                    Command cmd;
                    cmd.type = Command::Type::kSetOutput;
                    cmd.on = properties["on"].value<bool>();
                    if (!impl_->Post(cmd)) {
                        return std::unexpected(helmet::SanitizeMessage("output busy"));
                    }
                    return std::string("ok");
                });
}
