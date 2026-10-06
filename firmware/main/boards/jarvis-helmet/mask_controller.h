// Mask controller (FW2): two synchronized servos lifting the faceplate.
//
// Public API is shared with the sequence runner, the buttons and the HUD; keep it stable.
// All motion calls only queue a command and return immediately; the motion itself runs
// on the controller's own task (internal-RAM stack, it writes NVS).
#pragma once

#include <driver/gpio.h>

struct MaskState {
    enum class Kind { kClosed, kOpen, kMoving };
    Kind kind = Kind::kClosed;  // kMoving while a motion runs; idle at 0 -> kClosed, else kOpen
    int position = 0;           // current position, 0 = closed, 100 = open
    int target = 0;             // target of the current (or last) motion
};

// "closed" | "open" | "moving" (as returned by self.mask.get_state).
const char* MaskStateKindName(MaskState::Kind kind);

struct ServoCalibration {
    int closed_angle = 90;
    int open_angle = 90;
};

class MaskController {
public:
    // Reads calibration, default speed and the last position from HelmetStore, then starts
    // PWM at exactly the saved position so the servos do not jump at boot.
    MaskController(gpio_num_t s0_gpio, gpio_num_t s1_gpio);
    ~MaskController();

    // speed is 10..100; -1 uses the stored default (NVS mask_speed, 70 if unset).
    void Open(int speed = -1);
    void Close(int speed = -1);
    void SetPosition(int percent, int speed = -1);
    // Moves one servo to an absolute angle over duration_ms (0..5000), clamped to its
    // calibrated range. Used by the sequence "servo" step.
    void MoveServo(int id, int angle, int duration_ms);
    // Stops any motion and holds the current position (sequence stop, emergency stop).
    void Stop();

    // Returns true once no motion is running (or false on timeout).
    bool WaitIdle(int timeout_ms);
    bool IsMoving() const;
    MaskState GetState() const;
    int GetDefaultSpeed() const;

    // Calibration (user-only MCP tools). Calibrate() drives one servo to a raw angle 0..180
    // without clamping so the mechanical limits can be found; SaveCalibration() validates,
    // persists (s<N>_closed / s<N>_open) and applies the new limits.
    bool Calibrate(int servo, int angle);
    bool SaveCalibration(int servo, int closed_angle, int open_angle);
    ServoCalibration GetCalibration(int servo) const;

    // self.mask.open / close / set_position / get_state and the user-only
    // self.servo.calibrate / self.servo.save_calibration.
    void RegisterMcpTools();

    MaskController(const MaskController&) = delete;
    MaskController& operator=(const MaskController&) = delete;

private:
    // Implementation-defined members (LEDC channels, command queue, task, state, mutex).
    struct Impl;
    Impl* impl_ = nullptr;
};
