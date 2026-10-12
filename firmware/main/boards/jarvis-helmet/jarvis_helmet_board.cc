// Jarvis Helmet board: bring-up (FW1), buttons and offline behaviour (FW5) and the glue
// between the helmet controllers (FW2-FW4), the HUD (FW6) and the xiaozhi core.
//
// Started from bread-compact-wifi-lcd/compact_wifi_board_lcd.cc (xiaozhi-esp32, MIT),
// without its LampController: GPIO18 drives servo S1 here.
#include "application.h"
#include "assets/lang_config.h"
#include "button.h"
#include "config.h"
#include "display/lcd_display.h"
#include "led/single_led.h"
#include "ogg_demuxer.h"
#include "system_info.h"
#include "wifi_board.h"

#include "cf_access.h"
#include "helmet_common.h"
#include "helmet_store.h"
#include "helmet_tools.h"
#include "jarvis_hud_display.h"
#include "local_mcp_server.h"
#include "mask_controller.h"
#include "metered_audio_codec.h"
#include "output_controller.h"
#include "sequence_runner.h"
#include "vbus_monitor.h"

#include <driver/spi_common.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_log.h>
#include <esp_mac.h>
#include <esp_timer.h>
#include <wifi_manager.h>

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <utility>

#define TAG "JarvisHelmet"

namespace {

constexpr uint16_t kMaskLongPressMs = 800;  // mask button hold -> eye lights on/off
// A click is reported this long after release (espressif/button waits for a second
// press). PTT has no double-click action, so keep it short: interrupting Jarvis should
// silence the speaker within ~300 ms of the press. The mask button keeps the 180 ms
// default because it uses double-click.
constexpr uint16_t kPttClickGapMs = 80;
constexpr int kEyesPowerUpMs = 1200;
constexpr int kEyesFadeOutMs = 800;

// PTT released before the channel opened: stop listening as soon as it does. Opening can
// take the TCP/TLS connect plus up to 10 s waiting for the server hello.
constexpr uint32_t kPttPendingStopMs = 15000;
// Released while still Idle: StartListening may simply not have been processed yet.
constexpr uint32_t kPttPendingStopIdleMs = 2000;
// After a PTT click interrupted Jarvis, listen again if the session ends in Idle.
constexpr uint32_t kListenAfterAbortMs = 3000;
// Activation (OTA check) normally takes a few seconds; longer means the server is not
// answering and Application keeps retrying with growing delays.
constexpr uint32_t kActivationStallMs = 20000;

// CONFIG_JARVIS_WIFI_FAST_RESCAN (esp-wifi-connect defaults: 10 s .. 300 s).
constexpr int kWifiScanMinSeconds = 3;
constexpr int kWifiScanMaxSeconds = 30;

constexpr uint32_t kStatusPeriodMs = 1000;
constexpr uint32_t kSpeechLevelPeriodMs = 50;
// Heap stats every 10 s in every device state (IT1 E13 heap trend, tools/tests/e2e/heap_trend.py).
constexpr uint32_t kHeapLogEveryTicks = 10000 / kStatusPeriodMs;

uint32_t NowMs() { return static_cast<uint32_t>(esp_timer_get_time() / 1000); }

uint32_t DeadlineMs(uint32_t delay_ms) {
    uint32_t deadline = NowMs() + delay_ms;
    return deadline == 0 ? 1 : deadline;  // 0 means "not armed"
}

// True when `deadline` is armed and not yet reached (wrap-safe).
bool Pending(uint32_t deadline, uint32_t now) {
    return deadline != 0 && static_cast<int32_t>(deadline - now) > 0;
}

// Sequence "sound" step names mapped to the OGG files embedded by the core. Only files of
// at most 18 Opus packets: each fits into the empty decoder queue
// (MAX_DECODE_PACKETS_IN_QUEUE = 20 packets of any length), so queueing it never has to
// wait. The short effects use 60 ms packets; xin_chao ("Xin chào thế giới", VieNeu voice,
// assets/common/xin_chao.ogg) packs 1.3 s into 11 packets of 120 ms. The upstream spoken
// prompts (welcome, upgrade, activation, wificonfig: 27-88 packets) are not offered.
std::string_view SoundByName(const std::string& name) {
    struct Entry {
        const char* name;
        std::string_view sound;
    };
    static const Entry kSounds[] = {
        {"popup", Lang::Sounds::OGG_POPUP},
        {"success", Lang::Sounds::OGG_SUCCESS},
        {"exclamation", Lang::Sounds::OGG_EXCLAMATION},
        {"vibration", Lang::Sounds::OGG_VIBRATION},
        {"low_battery", Lang::Sounds::OGG_LOW_BATTERY},
        {"xin_chao", Lang::Sounds::OGG_XIN_CHAO},
    };
    for (const Entry& entry : kSounds) {
        if (name == entry.name) {
            return entry.sound;
        }
    }
    return {};
}

// Queues an embedded OGG sound for the sequence runner without ever blocking: the runner
// must notice a cancel within 20 ms. Upstream AudioService::PlaySound waits for room in
// the decoder queue, and while it waits it takes the slots TTS packets need (those are
// pushed without waiting and dropped when the queue is full). So the sound is skipped
// while Jarvis speaks or anything else is still playing, and packets are pushed without
// waiting (the rest is dropped if the queue ever fills up).
void QueueSound(const std::string& name, std::string_view ogg) {
    auto& app = Application::GetInstance();
    if (app.GetDeviceState() == kDeviceStateSpeaking) {
        ESP_LOGI(TAG, "Sound %s skipped: Jarvis is speaking", name.c_str());
        return;
    }
    AudioService& audio = app.GetAudioService();
    if (!audio.IsPlaybackIdle()) {
        ESP_LOGI(TAG, "Sound %s skipped: the speaker is busy", name.c_str());
        return;
    }
    auto demuxer = std::make_unique<OggDemuxer>();  // ~2.3 KB, kept off the runner stack
    bool queue_full = false;
    demuxer->OnPacket([&audio, &queue_full](const uint8_t* data, int sample_rate,
                                            int frame_duration_ms, size_t size) {
        if (queue_full) {
            return;
        }
        auto packet = std::make_unique<AudioStreamPacket>();
        packet->sample_rate = sample_rate;
        packet->frame_duration = frame_duration_ms;
        packet->payload.assign(data, data + size);
        if (!audio.PushPacketToDecodeQueue(std::move(packet), /*wait=*/false)) {
            queue_full = true;
        }
    });
    demuxer->Process(reinterpret_cast<const uint8_t*>(ogg.data()), ogg.size());
    if (queue_full) {
        ESP_LOGW(TAG, "Sound %s cut short: decoder queue full", name.c_str());
    }
}

// The mask button's emergency stop must act on the second press itself: espressif/button
// reports BUTTON_DOUBLE_CLICK only ~180 ms after the second release, and never when the
// second press is held longer than that. BUTTON_PRESS_REPEAT fires at the second press-down
// (button_handle_ is protected in the upstream Button).
class MaskButton : public Button {
public:
    using Button::Button;

    void OnPressRepeat(std::function<void()> callback) {
        if (button_handle_ == nullptr) {
            return;
        }
        on_press_repeat_ = std::move(callback);
        iot_button_register_cb(
            button_handle_, BUTTON_PRESS_REPEAT, nullptr,
            [](void* /*button_handle*/, void* usr_data) {
                auto* button = static_cast<MaskButton*>(usr_data);
                if (button->on_press_repeat_) {
                    button->on_press_repeat_();
                }
            },
            this);
    }

private:
    std::function<void()> on_press_repeat_;
};

}  // namespace

class JarvisHelmetBoard;

// Status LED (WS2812, unchanged upstream behaviour) that also forwards device state
// changes to the board. Application calls OnStateChanged() on the main task after every
// transition and on VAD changes while listening.
class JarvisStatusLed : public Led {
public:
    JarvisStatusLed(gpio_num_t gpio, JarvisHelmetBoard* board) : led_(gpio), board_(board) {}
    void OnStateChanged() override;

private:
    SingleLed led_;
    JarvisHelmetBoard* board_;
};

class JarvisHelmetBoard : public WifiBoard {
private:
    Button boot_button_;
    Button ptt_button_;
    MaskButton mask_button_;
    LcdDisplay* display_ = nullptr;
#if CONFIG_JARVIS_HUD
    JarvisHudDisplay* hud_ = nullptr;
#endif

    MaskController* mask_ = nullptr;
    OutputController* output_ = nullptr;
    VbusMonitor* vbus_ = nullptr;
    SequenceRunner* runner_ = nullptr;
    std::atomic<bool> tools_ready_{false};  // button callbacks may fire before InitializeTools
    esp_timer_handle_t status_timer_ = nullptr;
    uint32_t heap_log_ticks_ = 0;  // esp_timer task only
#if CONFIG_JARVIS_EYES_FOLLOW_STATE
    esp_timer_handle_t speech_timer_ = nullptr;
#endif

    // PTT state (esp_timer task writes, main task reads).
    std::atomic<bool> ptt_holding_{false};
    std::atomic<uint32_t> ptt_pending_stop_until_{0};
    std::atomic<uint32_t> listen_after_abort_until_{0};

    // Offline detection.
    enum WifiLink : int { kWifiUnknown, kWifiUp, kWifiDown };
    std::atomic<int> wifi_link_{kWifiUnknown};
    std::atomic<bool> server_unreachable_{false};  // last session attempt failed
    std::atomic<bool> ota_stalled_{false};         // boot: OTA check unanswered for 20 s
    std::atomic<uint32_t> activating_since_{0};
    std::mutex offline_mutex_;  // UpdateOffline() runs on several tasks
    int offline_shown_ = -1;
    DeviceState last_state_ = kDeviceStateUnknown;  // main task only

    static MeteredAudioCodec* Codec() {
        static MeteredAudioCodec codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
                                       AUDIO_I2S_SPK_GPIO_BCLK, AUDIO_I2S_SPK_GPIO_LRCK,
                                       AUDIO_I2S_SPK_GPIO_DOUT, AUDIO_I2S_MIC_GPIO_SCK,
                                       AUDIO_I2S_MIC_GPIO_WS, AUDIO_I2S_MIC_GPIO_DIN);
        return &codec;
    }

    void InitializeSpi() {
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = DISPLAY_MOSI_PIN;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = DISPLAY_CLK_PIN;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }

    void InitializeLcdDisplay() {
        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;

        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = DISPLAY_CS_PIN;
        io_config.dc_gpio_num = DISPLAY_DC_PIN;
        io_config.spi_mode = DISPLAY_SPI_MODE;
        io_config.pclk_hz = 40 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io));

        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = DISPLAY_RST_PIN;
        panel_config.rgb_ele_order = DISPLAY_RGB_ORDER;
        panel_config.bits_per_pixel = 16;
        ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io, &panel_config, &panel));

        esp_lcd_panel_reset(panel);
        esp_lcd_panel_init(panel);
        esp_lcd_panel_invert_color(panel, DISPLAY_INVERT_COLOR);
        esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY);
        esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);

#if CONFIG_JARVIS_HUD
        hud_ = new JarvisHudDisplay(panel_io, panel, DISPLAY_WIDTH, DISPLAY_HEIGHT,
                                    DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X,
                                    DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
        display_ = hud_;
#else
        display_ = new SpiLcdDisplay(panel_io, panel, DISPLAY_WIDTH, DISPLAY_HEIGHT,
                                     DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X,
                                     DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
#endif
    }

    // Button callbacks run on the esp_timer task: keep them short and never go through
    // Application::Schedule for the mask button (the main loop blocks while connecting).
    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            OnTalkClick();
        });

        // PTT: presses shorter than CONFIG_JARVIS_PTT_HOLD_MS are clicks, longer ones are
        // push-to-talk (espressif/button never reports a click for a long press).
        ptt_button_.OnClick([this]() { OnTalkClick(); });
        ptt_button_.OnLongPress([this]() { OnTalkHoldStart(); });
        ptt_button_.OnPressUp([this]() { OnTalkRelease(); });

        // Mask: works offline, never interrupts speech. The controllers and the runner only
        // queue work, so they are called directly from here. Double click = emergency stop,
        // taken at the second press-down (a double click never reports the single click).
        mask_button_.OnClick([this]() { ToggleMask(); });
        mask_button_.OnLongPress([this]() { ToggleEyes(); });
        mask_button_.OnPressRepeat([this]() { EmergencyStop(); });
    }

    void InitializeTools() {
        mask_ = new MaskController(SERVO_S0_GPIO, SERVO_S1_GPIO);
        output_ = new OutputController(EYES_GPIO, AUX_OUTPUT_GPIO);
        vbus_ = new VbusMonitor(VSENSE_ADC_UNIT, VSENSE_ADC_CHANNEL, VSENSE_DIVIDER_RATIO);
        vbus_->Start();

        auto show_info = MakeShowInfo();
        runner_ = new SequenceRunner(*mask_, *output_, show_info, MakePlaySound());
        runner_->Start();

        // First use of the codec, before the audio service starts: find the microphone slot.
        MeteredAudioCodec* codec = Codec();
#ifdef AUDIO_SPK_SD_GPIO
        codec->SetAmpEnableGpio(AUDIO_SPK_SD_GPIO);  // amp off until the first sound
#endif
        codec->ProbeMicrophone(AUDIO_I2S_MIC_GPIO_DIN);

        helmet_tools::Register(*runner_, *mask_, *output_, *vbus_, show_info, [codec]() {
            return helmet_tools::AudioLevels{
                codec->GetInputDbfs(), codec->GetOutputDbfs(),
                MeteredAudioCodec::MicProbeName(codec->mic_probe())};
        });
        mask_->RegisterMcpTools();
        output_->RegisterMcpTools();

#if CONFIG_JARVIS_HUD
        HudDataSource source;
        source.mask_state = [this]() { return mask_->GetState(); };
        source.eyes_state = [this](bool& on, int& brightness) {
            OutputState state = output_->GetState();
            on = state.eyes_on;
            brightness = state.eyes_brightness;
        };
        source.vbus_mv = [this]() { return vbus_->GetMillivolts(); };
        source.mic_level = [codec]() { return codec->GetInputLevel(); };
        source.speaker_level = [codec]() { return codec->GetOutputLevel(); };
        hud_->SetDataSource(std::move(source));
#endif

        cf_access::Init();
#if CONFIG_JARVIS_LOCAL_MCP_WS
        // NVS read here on the main task; the server starts later from the Wi-Fi event.
        LocalMcpServer::GetInstance().LoadToken();
#endif

        status_timer_ = CreateTimer(
            "jh_status", [](void* arg) { static_cast<JarvisHelmetBoard*>(arg)->OnStatusTimer(); });
        if (status_timer_ != nullptr) {
            esp_timer_start_periodic(status_timer_, kStatusPeriodMs * 1000);
        }
#if CONFIG_JARVIS_EYES_FOLLOW_STATE
        speech_timer_ = CreateTimer("jh_speech", [](void* arg) {
            auto* board = static_cast<JarvisHelmetBoard*>(arg);
            board->output_->SetSpeechLevel(Codec()->GetOutputLevel());
        });
#endif

        tools_ready_.store(true, std::memory_order_release);
    }

    esp_timer_handle_t CreateTimer(const char* name, esp_timer_cb_t callback) {
        esp_timer_create_args_t args = {};
        args.callback = callback;
        args.arg = this;
        args.dispatch_method = ESP_TIMER_TASK;
        args.name = name;
        args.skip_unhandled_events = true;
        esp_timer_handle_t timer = nullptr;
        if (esp_timer_create(&args, &timer) != ESP_OK) {
            ESP_LOGE(TAG, "Timer %s not created", name);
            return nullptr;
        }
        return timer;
    }

    SequenceRunner::ShowInfoFn MakeShowInfo() {
#if CONFIG_JARVIS_HUD
        return [this](const std::string& title, const std::string& text, int seconds) {
            hud_->ShowInfo(title, text, seconds);
        };
#else
        // Stock UI: one-line notification in the status bar plus the text as a system
        // chat line (cleared by the next state change).
        return [this](const std::string& title, const std::string& text, int seconds) {
            if (!title.empty()) {  // a text-only card leaves the status bar alone
                display_->ShowNotification(title, seconds * 1000);
            }
            display_->SetChatMessage("system", text.c_str());
        };
#endif
    }

    // Runs on the sequence runner task and never blocks (see QueueSound).
    static SequenceRunner::PlaySoundFn MakePlaySound() {
        return [](const std::string& name) {
            std::string_view sound = SoundByName(name);
            if (sound.empty()) {
                return false;
            }
            QueueSound(name, sound);
            return true;
        };
    }

    bool ToolsReady() const { return tools_ready_.load(std::memory_order_acquire); }

    // ---- PTT / BOOT ---------------------------------------------------------------

    void OnTalkClick() {
        auto& app = Application::GetInstance();
        if (app.GetDeviceState() == kDeviceStateSpeaking) {
            // Interrupt: tell the server to stop and flush local playback right away. The
            // server answers with "tts stop", which returns to listening in auto mode;
            // a manual (PTT hold) session lands in Idle instead and is restarted by
            // OnDeviceStateChanged().
            listen_after_abort_until_.store(DeadlineMs(kListenAfterAbortMs));
            app.Schedule([]() {
                auto& app = Application::GetInstance();
                if (app.GetDeviceState() == kDeviceStateSpeaking) {
                    app.AbortSpeaking(kAbortReasonNone);
                    app.GetAudioService().ResetDecoder();
                }
            });
            return;
        }
        app.ToggleChatState();
    }

    void OnTalkHoldStart() {
        ptt_holding_.store(true);
        ptt_pending_stop_until_.store(0);
        // Speaking: aborts and flushes playback, then listens (manual stop).
        Application::GetInstance().StartListening();
    }

    void OnTalkRelease() {
        if (!ptt_holding_.exchange(false)) {
            return;  // end of a click, handled by OnClick
        }
        auto& app = Application::GetInstance();
        DeviceState state = app.GetDeviceState();
        if (state == kDeviceStateConnecting) {
            // StopListening is ignored while connecting: remember it for later.
            ptt_pending_stop_until_.store(DeadlineMs(kPttPendingStopMs));
        } else if (state == kDeviceStateIdle) {
            ptt_pending_stop_until_.store(DeadlineMs(kPttPendingStopIdleMs));
        }
        app.StopListening();
    }

    // ---- Mask button ----------------------------------------------------------------

    // Closed, or moving toward closed -> open_mask; otherwise close_mask. A running mask
    // sequence tells the direction even before the servos start moving.
    const char* NextMaskSequence() {
        std::string running = runner_->CurrentName();
        if (running == "open_mask") {
            return "close_mask";
        }
        if (running == "close_mask") {
            return "open_mask";
        }
        MaskState state = mask_->GetState();
        bool closing = state.kind == MaskState::Kind::kMoving &&
                       (state.target == helmet::kPositionMin || state.target < state.position);
        return (state.kind == MaskState::Kind::kClosed || closing) ? "open_mask" : "close_mask";
    }

    void ToggleMask() {
        if (!ToolsReady()) {
            return;
        }
        const char* name = NextMaskSequence();
        auto result = runner_->RunSaved(name);
        if (result) {
            ESP_LOGI(TAG, "Mask button: %s", name);
        } else {
            ESP_LOGW(TAG, "Mask button: %s failed: %s", name, result.error().c_str());
        }
    }

    void ToggleEyes() {
        if (!ToolsReady()) {
            return;
        }
        OutputState state = output_->GetState();
        bool lit =
            state.eyes_effect == "power_up" || (state.eyes_on && state.eyes_effect != "fade_out");
        if (lit) {
            output_->EyesEffect("fade_out", kEyesFadeOutMs);
        } else {
            output_->EyesEffect("power_up", kEyesPowerUpMs);
        }
    }

    void EmergencyStop() {
        if (!ToolsReady()) {
            return;
        }
        runner_->Stop();
        mask_->Stop();
        ESP_LOGW(TAG, "Mask button: emergency stop");
    }

    // ---- Offline indicator --------------------------------------------------------

    void HandleNetworkEvent(NetworkEvent event) {
        switch (event) {
            case NetworkEvent::Connected:
                wifi_link_.store(kWifiUp);
                cf_access::RefreshHosts();  // the OTA URL may have changed in the portal
#if CONFIG_JARVIS_LOCAL_MCP_WS
                LocalMcpServer::GetInstance().Start(CONFIG_JARVIS_LOCAL_MCP_WS_PORT);
#endif
                break;
            case NetworkEvent::Scanning:
            case NetworkEvent::Disconnected:
                // Scanning at boot is not "offline" yet: only a link we had is lost.
                if (wifi_link_.load() == kWifiUp) {
                    wifi_link_.store(kWifiDown);
                }
                break;
            case NetworkEvent::WifiConfigModeEnter:
                wifi_link_.store(kWifiDown);
                break;
            default:
                break;
        }
        UpdateOffline();
    }

    void OnStatusTimer() {
        // Upstream prints the heap from its clock timer but restarts that count on every state
        // change, so it goes silent exactly while the helmet is busy (a 50-command E13 run).
        if (++heap_log_ticks_ % kHeapLogEveryTicks == 0) {
            SystemInfo::PrintHeapStats();
        }
        auto& app = Application::GetInstance();
        // Set by the activation task once the OTA check got an answer: an activation that
        // waits for the user to bind a code is not "offline".
        const bool ota_answered = app.OtaCheckSucceeded();
        if (ota_stalled_.load()) {
            if (ota_answered) {
                // A later retry got through (server or tunnel came up after the helmet).
                ESP_LOGI(TAG, "OTA check answered");
                ota_stalled_.store(false);
                UpdateOffline();
            }
            return;
        }
        if (!ota_answered && wifi_link_.load() == kWifiUp &&
            app.GetDeviceState() == kDeviceStateActivating) {
            uint32_t since = activating_since_.load();
            if (since != 0 && NowMs() - since > kActivationStallMs) {
                ESP_LOGW(TAG, "Server not answering the OTA check");
                ota_stalled_.store(true);
                UpdateOffline();
            }
        }
    }

    void UpdateOffline() {
        std::lock_guard<std::mutex> lock(offline_mutex_);
        bool offline =
            wifi_link_.load() == kWifiDown || server_unreachable_.load() || ota_stalled_.load();
        int value = offline ? 1 : 0;
        if (offline_shown_ == value) {
            return;
        }
        offline_shown_ = value;
        ESP_LOGI(TAG, "%s", offline ? "OFFLINE" : "Online");
#if CONFIG_JARVIS_HUD
        if (hud_ != nullptr) {
            hud_->SetOffline(offline);
        }
#endif
    }

public:
    JarvisHelmetBoard()
        : boot_button_(BOOT_BUTTON_GPIO),
          ptt_button_(PTT_BUTTON_GPIO, false, CONFIG_JARVIS_PTT_HOLD_MS, kPttClickGapMs),
          mask_button_(MASK_BUTTON_GPIO, false, kMaskLongPressMs) {
        HelmetStore::GetInstance().Init();
        InitializeSpi();
        InitializeLcdDisplay();
        InitializeButtons();
        InitializeTools();
        if (DISPLAY_BACKLIGHT_PIN != GPIO_NUM_NC) {
            GetBacklight()->RestoreBrightness();
        }
    }

    Led* GetLed() override {
        static JarvisStatusLed led(BUILTIN_LED_GPIO, this);
        return &led;
    }

    AudioCodec* GetAudioCodec() override { return Codec(); }

    Display* GetDisplay() override { return display_; }

    Backlight* GetBacklight() override {
        if (DISPLAY_BACKLIGHT_PIN != GPIO_NUM_NC) {
            static PwmBacklight backlight(DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
            return &backlight;
        }
        return nullptr;
    }

    void StartNetwork() override {
#if CONFIG_JARVIS_WIFI_FAST_RESCAN
        // WifiManager keeps the configuration of its first Initialize() call. Initialize it
        // here with the settings WifiBoard::StartNetwork() uses plus shorter station rescan
        // intervals; the base call below then logs "Already initialized", installs its
        // event callback and connects. Keep the fields in sync with wifi_board.cc.
        WifiManagerConfig config;
        config.ssid_prefix = "Xiaozhi";
        config.language = Lang::CODE;
        config.show_ota_config = true;
        config.show_sleep_config = true;
        config.station_scan_min_interval_seconds = kWifiScanMinSeconds;
        config.station_scan_max_interval_seconds = kWifiScanMaxSeconds;
        uint8_t mac[6];
        if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
            char hostname[32];
            snprintf(hostname, sizeof(hostname), "%s-%02X%02X", config.ssid_prefix.c_str(), mac[4],
                     mac[5]);
            config.station_hostname = hostname;
        }
        WifiManager::GetInstance().Initialize(config);
#endif
        WifiBoard::StartNetwork();
    }

    // Application owns the only network callback; wrap it to track the link state.
    void SetNetworkEventCallback(NetworkEventCallback callback) override {
        WifiBoard::SetNetworkEventCallback(
            [this, callback = std::move(callback)](NetworkEvent event, const std::string& data) {
                HandleNetworkEvent(event);
                if (callback) {
                    callback(event, data);
                }
            });
    }

    // Main task, after every device state change (see JarvisStatusLed).
    void OnDeviceStateChanged() {
        auto& app = Application::GetInstance();
        const DeviceState state = app.GetDeviceState();
        const DeviceState previous = last_state_;
        if (state == previous || !ToolsReady()) {
            return;  // VAD updates while listening also come through here
        }
        last_state_ = state;
        const uint32_t now = NowMs();

        output_->OnDeviceStateChanged(static_cast<int>(state));
#if CONFIG_JARVIS_EYES_FOLLOW_STATE
        if (speech_timer_ != nullptr) {
            if (state == kDeviceStateSpeaking) {
                esp_timer_start_periodic(speech_timer_, kSpeechLevelPeriodMs * 1000);
            } else if (previous == kDeviceStateSpeaking) {
                esp_timer_stop(speech_timer_);
            }
        }
#endif

        switch (state) {
            case kDeviceStateConnecting:
                // The OTA check may have handed out a new websocket URL.
                cf_access::RefreshHosts();
                break;
            case kDeviceStateListening:
                server_unreachable_.store(false);  // the channel is open
                ota_stalled_.store(false);
                listen_after_abort_until_.store(0);
                if (Pending(ptt_pending_stop_until_.exchange(0), now)) {
                    ESP_LOGI(TAG, "PTT was released while connecting: stop listening");
                    app.StopListening();
                }
                break;
            case kDeviceStateSpeaking:
                server_unreachable_.store(false);
                ota_stalled_.store(false);
                break;
            case kDeviceStateIdle:
                if (previous == kDeviceStateConnecting) {
                    // Every successful open goes Connecting -> Listening.
                    server_unreachable_.store(true);
                    ptt_pending_stop_until_.store(0);
                } else if (previous == kDeviceStateSpeaking &&
                           Pending(listen_after_abort_until_.exchange(0), now)) {
                    ESP_LOGI(TAG, "Interrupted a manual session: listening again");
                    app.ToggleChatState();
                }
                break;
            case kDeviceStateActivating:
                activating_since_.store(now == 0 ? 1 : now);
                break;
            default:
                break;
        }
        UpdateOffline();
    }
};

void JarvisStatusLed::OnStateChanged() {
    led_.OnStateChanged();
    board_->OnDeviceStateChanged();
}

DECLARE_BOARD(JarvisHelmetBoard);
