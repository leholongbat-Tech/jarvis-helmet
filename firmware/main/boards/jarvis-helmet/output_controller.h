// Eye lights and spare output (FW3).
//
// Public API is shared with the sequence runner, the buttons and the HUD; keep it stable.
// Calls only queue work and return immediately; effects run on the controller's task.
#pragma once

#include <driver/gpio.h>

#include <string>

struct OutputState {
    bool eyes_on = false;
    int eyes_brightness = 0;   // 0..100 target brightness, 0 when off
    std::string eyes_effect;   // running effect name, empty when none
    bool output_on = false;
};

class OutputController {
public:
    // Both channels start OFF (the gate pull-downs keep them off during boot too).
    OutputController(gpio_num_t eyes_gpio, gpio_num_t output_gpio);
    ~OutputController();

    // brightness 0..100; -1 uses the stored default (NVS eyes_bright, 100 if unset).
    // Cancels any running effect.
    void SetEyes(bool on, int brightness = -1);
    // name: breathe | blink | flicker | power_up | fade_out; duration_ms 100..10000
    // (for breathe it is the cycle period). A new effect cancels the running one.
    // Returns false for an unknown name.
    bool EyesEffect(const std::string& name, int duration_ms);
    // Cancels the running effect and keeps the current brightness.
    void StopEffect();
    void SetOutput(bool on);

    // Returns true once no finite effect is running; breathe counts as idle.
    bool WaitIdle(int timeout_ms);
    OutputState GetState() const;
    int GetDefaultBrightness() const;

    // self.eyes.set, self.eyes.effect, self.output.set
    void RegisterMcpTools();

    // Optional assistant-state linkage (CONFIG_JARVIS_EYES_FOLLOW_STATE). Called by the
    // board on every device state change (DeviceState value) and with the speaker level
    // (0..1) while speaking. No-ops when the option is off.
    void OnDeviceStateChanged(int device_state);
    void SetSpeechLevel(float level);

    OutputController(const OutputController&) = delete;
    OutputController& operator=(const OutputController&) = delete;

private:
    struct Impl;
    Impl* impl_ = nullptr;
};
