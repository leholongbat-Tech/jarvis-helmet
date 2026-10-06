// Host tests for actuator_math.h: servo pulse/duty math and the mask motion profile (FW2),
// gamma-corrected light levels and effect curves (FW3).
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <cstdint>
#include <cstdlib>

#include "actuator_math.h"
#include "helmet_common.h"

using Catch::Approx;

namespace {

// Values from config.h / contract §3.3.
constexpr int kPulseMinUs = 500;
constexpr int kPulseMaxUs = 2400;
constexpr int kServoBits = 14;
constexpr int kServoHz = 50;
constexpr int kEyesBits = 10;
constexpr uint32_t kEyesMaxDuty = 1024;
constexpr float kGamma = 2.2f;

uint32_t ServoDuty(float angle) {
    return actuator::PulseUsToDuty(actuator::AngleToPulseUs(angle, kPulseMinUs, kPulseMaxUs),
                                   kServoBits, kServoHz);
}

}  // namespace

TEST_CASE("AngleToPulseUs maps 0..180 degrees onto the pulse range", "[actuator][servo]") {
    REQUIRE(actuator::AngleToPulseUs(0.0f, kPulseMinUs, kPulseMaxUs) == Approx(500.0f));
    REQUIRE(actuator::AngleToPulseUs(45.0f, kPulseMinUs, kPulseMaxUs) == Approx(975.0f));
    REQUIRE(actuator::AngleToPulseUs(90.0f, kPulseMinUs, kPulseMaxUs) == Approx(1450.0f));
    REQUIRE(actuator::AngleToPulseUs(180.0f, kPulseMinUs, kPulseMaxUs) == Approx(2400.0f));
    // Out-of-range angles are clamped instead of overdriving the servo.
    REQUIRE(actuator::AngleToPulseUs(-20.0f, kPulseMinUs, kPulseMaxUs) == Approx(500.0f));
    REQUIRE(actuator::AngleToPulseUs(200.0f, kPulseMinUs, kPulseMaxUs) == Approx(2400.0f));
}

TEST_CASE("PulseUsToDuty converts a pulse width to LEDC duty", "[actuator][servo]") {
    REQUIRE(actuator::MaxDuty(kServoBits) == 16384u);
    REQUIRE(actuator::MaxDuty(kEyesBits) == kEyesMaxDuty);
    // 14 bit at 50 Hz: duty = pulse_us * 16384 / 20000, rounded.
    REQUIRE(actuator::PulseUsToDuty(500.0f, kServoBits, kServoHz) == 410u);    // 409.6
    REQUIRE(actuator::PulseUsToDuty(1500.0f, kServoBits, kServoHz) == 1229u);  // 1228.8
    REQUIRE(actuator::PulseUsToDuty(2400.0f, kServoBits, kServoHz) == 1966u);  // 1966.08
    REQUIRE(actuator::PulseUsToDuty(20000.0f, kServoBits, kServoHz) == 16384u);
    REQUIRE(actuator::PulseUsToDuty(30000.0f, kServoBits, kServoHz) == 16384u);  // clamped
    REQUIRE(actuator::PulseUsToDuty(0.0f, kServoBits, kServoHz) == 0u);
    REQUIRE(actuator::PulseUsToDuty(-5.0f, kServoBits, kServoHz) == 0u);
    REQUIRE(actuator::PulseUsToDuty(1000.0f, kServoBits, 0) == 0u);
    // 10 bit at 5 kHz: 100 us of a 200 us period is half scale.
    REQUIRE(actuator::PulseUsToDuty(100.0f, kEyesBits, 5000) == 512u);
}

TEST_CASE("Servo duty grows monotonically and stays within 500..2400 us", "[actuator][servo]") {
    uint32_t previous = 0;
    for (int angle = 0; angle <= 180; ++angle) {
        const uint32_t duty = ServoDuty(static_cast<float>(angle));
        REQUIRE(duty >= previous);
        REQUIRE(duty >= 410u);
        REQUIRE(duty <= 1966u);
        previous = duty;
    }
    // One degree is ~8.6 counts at 14 bit: fine enough for smooth easing.
    REQUIRE(ServoDuty(91.0f) - ServoDuty(90.0f) >= 8u);
}

TEST_CASE("Calibration pairs need two different angles within 0..180", "[actuator][servo]") {
    REQUIRE(actuator::IsValidCalibration(80, 100));
    REQUIRE(actuator::IsValidCalibration(100, 80));
    REQUIRE(actuator::IsValidCalibration(0, 180));
    REQUIRE_FALSE(actuator::IsValidCalibration(90, 90));
    REQUIRE_FALSE(actuator::IsValidCalibration(-1, 90));
    REQUIRE_FALSE(actuator::IsValidCalibration(90, 181));
}

TEST_CASE("PositionToAngle handles the normal and the mirrored servo", "[actuator][servo]") {
    // S0 default calibration: closed 80, open 100.
    REQUIRE(actuator::PositionToAngle(0.0f, 80, 100) == Approx(80.0f));
    REQUIRE(actuator::PositionToAngle(50.0f, 80, 100) == Approx(90.0f));
    REQUIRE(actuator::PositionToAngle(100.0f, 80, 100) == Approx(100.0f));
    // S1 is mounted mirrored: closed 100, open 80, no invert flag needed.
    REQUIRE(actuator::PositionToAngle(0.0f, 100, 80) == Approx(100.0f));
    REQUIRE(actuator::PositionToAngle(25.0f, 100, 80) == Approx(95.0f));
    REQUIRE(actuator::PositionToAngle(100.0f, 100, 80) == Approx(80.0f));
    // Positions outside 0..100 are clamped.
    REQUIRE(actuator::PositionToAngle(-10.0f, 100, 80) == Approx(100.0f));
    REQUIRE(actuator::PositionToAngle(150.0f, 80, 100) == Approx(100.0f));
}

TEST_CASE("AngleToPosition inverts PositionToAngle", "[actuator][servo]") {
    for (int position = 0; position <= 100; position += 5) {
        const float p = static_cast<float>(position);
        const float a0 = actuator::PositionToAngle(p, 75, 160);
        const float a1 = actuator::PositionToAngle(p, 160, 75);
        REQUIRE(actuator::AngleToPosition(a0, 75, 160) == Approx(p).margin(1e-3));
        REQUIRE(actuator::AngleToPosition(a1, 160, 75) == Approx(p).margin(1e-3));
    }
    // Beyond the calibrated range the position saturates.
    REQUIRE(actuator::AngleToPosition(60.0f, 80, 100) == Approx(0.0f));
    REQUIRE(actuator::AngleToPosition(120.0f, 80, 100) == Approx(100.0f));
    REQUIRE(actuator::AngleToPosition(120.0f, 100, 80) == Approx(0.0f));
    // A degenerate pair never divides by zero.
    REQUIRE(actuator::AngleToPosition(90.0f, 90, 90) == Approx(0.0f));
}

TEST_CASE("ClampToCalibration keeps angles inside the calibrated range", "[actuator][servo]") {
    REQUIRE(actuator::ClampToCalibration(45.0f, 80, 100) == Approx(80.0f));
    REQUIRE(actuator::ClampToCalibration(170.0f, 80, 100) == Approx(100.0f));
    REQUIRE(actuator::ClampToCalibration(90.0f, 80, 100) == Approx(90.0f));
    // Same range whatever the order of closed and open.
    REQUIRE(actuator::ClampToCalibration(45.0f, 100, 80) == Approx(80.0f));
    REQUIRE(actuator::ClampToCalibration(170.0f, 100, 80) == Approx(100.0f));
    REQUIRE(actuator::ClampToCalibration(85.5f, 100, 80) == Approx(85.5f));
}

TEST_CASE("Cosine ease-in-out is monotonic, symmetric and gentle at both ends",
          "[actuator][motion]") {
    REQUIRE(actuator::EaseInOutCosine(0.0f) == Approx(0.0f).margin(1e-6));
    REQUIRE(actuator::EaseInOutCosine(0.5f) == Approx(0.5f).margin(1e-6));
    REQUIRE(actuator::EaseInOutCosine(1.0f) == Approx(1.0f).margin(1e-6));
    REQUIRE(actuator::EaseInOutCosine(-1.0f) == Approx(0.0f).margin(1e-6));
    REQUIRE(actuator::EaseInOutCosine(2.0f) == Approx(1.0f).margin(1e-6));
    float previous = 0.0f;
    for (int i = 1; i <= 100; ++i) {
        const float t = static_cast<float>(i) / 100.0f;
        const float e = actuator::EaseInOutCosine(t);
        REQUIRE(e >= previous);
        REQUIRE(e + actuator::EaseInOutCosine(1.0f - t) == Approx(1.0f).margin(1e-5));
        previous = e;
    }
    // The first and last 5 % of the time cover less than 1 % of the travel.
    REQUIRE(actuator::EaseInOutCosine(0.05f) < 0.01f);
    REQUIRE(actuator::EaseInOutCosine(0.95f) > 0.99f);
}

TEST_CASE("EasedValue holds before the start and lands exactly on the target",
          "[actuator][motion]") {
    REQUIRE(actuator::EasedValue(80.0f, 100.0f, -30.0f, 1000.0f) == Approx(80.0f));
    REQUIRE(actuator::EasedValue(80.0f, 100.0f, 0.0f, 1000.0f) == Approx(80.0f));
    REQUIRE(actuator::EasedValue(80.0f, 100.0f, 500.0f, 1000.0f) == Approx(90.0f));
    REQUIRE(actuator::EasedValue(80.0f, 100.0f, 1000.0f, 1000.0f) == 100.0f);
    REQUIRE(actuator::EasedValue(80.0f, 100.0f, 5000.0f, 1000.0f) == 100.0f);
    REQUIRE(actuator::EasedValue(100.0f, 80.0f, 500.0f, 1000.0f) == Approx(90.0f));
    REQUIRE(actuator::EasedValue(80.0f, 100.0f, 0.0f, 0.0f) == 100.0f);  // zero time = jump
}

TEST_CASE("Full mask travel at speed 70 takes 1.1 +- 0.2 s (FW2 acceptance 2)",
          "[actuator][motion]") {
    const int ms = helmet::MaskMoveMs(0, 100, 70);
    REQUIRE(ms >= 900);
    REQUIRE(ms <= 1300);
    REQUIRE(helmet::MaskMoveMs(100, 0, 70) == ms);
}

TEST_CASE("Staggered start delay is capped at half the move", "[actuator][motion]") {
    REQUIRE(actuator::StaggerDelayMs(1142, 30) == 30);
    REQUIRE(actuator::StaggerDelayMs(150, 30) == 30);
    REQUIRE(actuator::StaggerDelayMs(40, 30) == 20);
    REQUIRE(actuator::StaggerDelayMs(1000, 0) == 0);
    REQUIRE(actuator::StaggerDelayMs(0, 30) == 0);
}

TEST_CASE("TravelMs scales with the angle and has a floor", "[actuator][motion]") {
    REQUIRE(actuator::TravelMs(90.0f, 90.0f, 6.0f, 150) == 150);
    REQUIRE(actuator::TravelMs(0.0f, 180.0f, 6.0f, 150) == 1080);
    REQUIRE(actuator::TravelMs(120.0f, 100.0f, 6.0f, 150) == 150);
    REQUIRE(actuator::TravelMs(100.0f, 150.0f, 6.0f, 150) == 300);
}

namespace {

// MaskController::StartMaskMove with the default calibration (S0 80/100, S1 100/80).
int MoveMs(float s0_from, float s1_from, int position, int speed) {
    const int closed[2] = {80, 100};
    const int open[2] = {100, 80};
    const float from[2] = {s0_from, s1_from};
    float to[2];
    for (int s = 0; s < 2; ++s) {
        to[s] = actuator::ClampToCalibration(
            actuator::PositionToAngle(static_cast<float>(position), closed[s], open[s]),
            closed[s], open[s]);
    }
    return actuator::MaskMoveDurationMs(from, to, closed, open, 2,
                                        helmet::MaskFullTravelMs(speed), helmet::kMaskMinMoveMs,
                                        helmet::MaskFullTravelMs(helmet::kSpeedMin));
}

}  // namespace

TEST_CASE("Mask move duration inside the calibrated range equals MaskMoveMs",
          "[actuator][motion]") {
    // Both servos at the same position p: S0 = 80 + 0.2 p, S1 = 100 - 0.2 p.
    const int positions[] = {0, 13, 30, 50, 71, 99, 100};
    for (int from : positions) {
        for (int to : positions) {
            for (int speed : {10, 55, 70, 80, 100}) {
                const float s0 = 80.0f + 0.2f * static_cast<float>(from);
                const float s1 = 100.0f - 0.2f * static_cast<float>(from);
                CAPTURE(from, to, speed);
                CHECK(MoveMs(s0, s1, to, speed) == helmet::MaskMoveMs(from, to, speed));
            }
        }
    }
    CHECK(MoveMs(80.0f, 100.0f, 100, 70) == 1142);  // closed -> open
    CHECK(MoveMs(100.0f, 80.0f, 0, 80) == 1000);    // open -> closed
}

TEST_CASE("Mask move from beyond the calibrated range eases instead of snapping",
          "[actuator][motion]") {
    // Raw calibration left S0 at 150 deg (calibrated open = 100); S1 is open already.
    // Clamped positions would both read 100 = target, i.e. a 0 ms snap of 50 degrees.
    CHECK(MoveMs(150.0f, 80.0f, 100, 70) == 2855);  // 250 % of the span at 1142 ms per span
    // Explored below the closed angle, then "close": 40 -> 80 deg is 200 % of the span.
    CHECK(MoveMs(40.0f, 100.0f, 0, 70) == 2284);
    // Far beyond at the slowest speed: capped at a full travel at speed 10 (8 s), below the
    // sequence runner's motion timeout.
    CHECK(MoveMs(150.0f, 80.0f, 100, 10) == 8000);
    // The other servo farther away decides.
    CHECK(MoveMs(150.0f, 100.0f, 100, 70) == 2855);
}

TEST_CASE("Mask move settles in place only when already there", "[actuator][motion]") {
    CHECK(MoveMs(100.0f, 80.0f, 100, 70) == 0);
    CHECK(MoveMs(99.9f, 80.1f, 100, 70) == 0);  // 99.5 % rounds to 100 %, 0.1 deg away
    // One percent of the span gets the minimum eased move, as with MaskMoveMs.
    CHECK(MoveMs(99.8f, 80.2f, 100, 70) == helmet::kMaskMinMoveMs);
    CHECK(helmet::MaskMoveMs(99, 100, 70) == helmet::kMaskMinMoveMs);
    // Wide calibration (0..180): 0.6 deg is less than one percent but still moves.
    const int closed[1] = {0};
    const int open[1] = {180};
    const float from[1] = {90.6f};
    const float to[1] = {90.0f};
    CHECK(actuator::MaskMoveDurationMs(from, to, closed, open, 1, 1142, 150, 8000) == 150);
    const float near[1] = {90.3f};
    CHECK(actuator::MaskMoveDurationMs(near, to, closed, open, 1, 1142, 150, 8000) == 0);
}

TEST_CASE("BootAngle restores each servo's own resting angle", "[actuator][servo]") {
    // Stored angle inside the calibration: exactly that angle, whatever mask_pos says.
    CHECK(actuator::BootAngle(100, 50.0f, 80, 100) == Approx(100.0f));
    CHECK(actuator::BootAngle(80, 50.0f, 100, 80) == Approx(80.0f));  // mirrored S1
    // Left beyond the range by raw calibration: back to the nearest limit.
    CHECK(actuator::BootAngle(150, 100.0f, 80, 100) == Approx(100.0f));
    CHECK(actuator::BootAngle(40, 0.0f, 80, 100) == Approx(80.0f));
    // No (or an invalid) stored angle: the angle of the stored mask position.
    CHECK(actuator::BootAngle(-1, 50.0f, 80, 100) == Approx(90.0f));
    CHECK(actuator::BootAngle(200, 0.0f, 100, 80) == Approx(100.0f));
    // The FW2 scenario: S0 moved alone to open (100 deg) while S1 stayed closed (100 deg).
    // mask_pos = 50 would drive both servos to 90 deg; the stored angles keep both still.
    CHECK(actuator::BootAngle(100, 50.0f, 80, 100) == Approx(100.0f));
    CHECK(actuator::BootAngle(100, 50.0f, 100, 80) == Approx(100.0f));
}

TEST_CASE("Simulated mask open: both servos in sync, smooth, within calibration",
          "[actuator][motion]") {
    // Same scheme as MaskController: 20 ms ticks, S1 starts 30 ms later and runs for the
    // remaining time, so both servos end together.
    struct Calibration {
        int closed;
        int open;
    };
    const Calibration servos[2] = {{75, 160}, {160, 75}};
    const int total_ms = helmet::MaskMoveMs(0, 100, 70);
    const int delay_ms = actuator::StaggerDelayMs(total_ms, 30);
    const int start_ms[2] = {0, delay_ms};
    const int duration_ms[2] = {total_ms, total_ms - delay_ms};

    float previous[2] = {75.0f, 160.0f};
    int finished_at[2] = {-1, -1};
    for (int t = 0; t <= total_ms + 40; t += 20) {
        for (int s = 0; s < 2; ++s) {
            const float from = actuator::PositionToAngle(0.0f, servos[s].closed, servos[s].open);
            const float to = actuator::PositionToAngle(100.0f, servos[s].closed, servos[s].open);
            const float angle = actuator::EasedValue(from, to, static_cast<float>(t - start_ms[s]),
                                                     static_cast<float>(duration_ms[s]));
            REQUIRE(angle >= 75.0f - 1e-3f);
            REQUIRE(angle <= 160.0f + 1e-3f);
            // 85 degrees in ~1.1 s with cosine easing: at most ~2.4 degrees per tick.
            REQUIRE(std::fabs(angle - previous[s]) <= 3.0f);
            if (s == 0) {
                REQUIRE(angle >= previous[s] - 1e-4f);
            } else {
                REQUIRE(angle <= previous[s] + 1e-4f);  // mirrored servo turns the other way
            }
            if (finished_at[s] < 0 && angle == to) {
                finished_at[s] = t;
            }
            previous[s] = angle;
        }
    }
    REQUIRE(finished_at[0] >= 0);
    REQUIRE(finished_at[1] >= 0);
    REQUIRE(std::abs(finished_at[0] - finished_at[1]) < 50);  // FW2 acceptance 1
}

TEST_CASE("GammaDuty maps perceived brightness to duty", "[actuator][light]") {
    REQUIRE(actuator::GammaDuty(0.0f, kEyesMaxDuty, kGamma) == 0u);
    REQUIRE(actuator::GammaDuty(-5.0f, kEyesMaxDuty, kGamma) == 0u);
    REQUIRE(actuator::GammaDuty(100.0f, kEyesMaxDuty, kGamma) == kEyesMaxDuty);
    REQUIRE(actuator::GammaDuty(150.0f, kEyesMaxDuty, kGamma) == kEyesMaxDuty);
    // duty = 1024 * (b / 100)^2.2
    REQUIRE(actuator::GammaDuty(50.0f, kEyesMaxDuty, kGamma) == 223u);  // 222.9
    REQUIRE(actuator::GammaDuty(10.0f, kEyesMaxDuty, kGamma) == 6u);    // 6.46
    // A dim but non-zero setting never switches the LED off.
    REQUIRE(actuator::GammaDuty(1.0f, kEyesMaxDuty, kGamma) == 1u);
    REQUIRE(actuator::GammaDuty(0.1f, kEyesMaxDuty, kGamma) == 1u);
    REQUIRE(actuator::GammaDuty(50.0f, 0, kGamma) == 0u);
    uint32_t previous = 0;
    for (int b = 0; b <= 100; ++b) {
        const uint32_t duty = actuator::GammaDuty(static_cast<float>(b), kEyesMaxDuty, kGamma);
        REQUIRE(duty >= previous);
        previous = duty;
    }
    // 10 %, 50 % and 100 % are clearly different steps (FW3 acceptance 3).
    REQUIRE(actuator::GammaDuty(10.0f, kEyesMaxDuty, kGamma) * 10 <
            actuator::GammaDuty(50.0f, kEyesMaxDuty, kGamma));
    REQUIRE(actuator::GammaDuty(50.0f, kEyesMaxDuty, kGamma) * 4 <
            actuator::GammaDuty(100.0f, kEyesMaxDuty, kGamma));
}

TEST_CASE("DutyToBrightness inverts the gamma mapping", "[actuator][light]") {
    REQUIRE(actuator::DutyToBrightness(0, kEyesMaxDuty, kGamma) == 0.0f);
    REQUIRE(actuator::DutyToBrightness(kEyesMaxDuty, kEyesMaxDuty, kGamma) == 100.0f);
    REQUIRE(actuator::DutyToBrightness(2000, kEyesMaxDuty, kGamma) == 100.0f);
    REQUIRE(actuator::DutyToBrightness(10, kEyesMaxDuty, 0.0f) == 0.0f);
    for (int b = 10; b <= 100; b += 10) {
        const float level = static_cast<float>(b);
        const uint32_t duty = actuator::GammaDuty(level, kEyesMaxDuty, kGamma);
        REQUIRE(actuator::DutyToBrightness(duty, kEyesMaxDuty, kGamma) ==
                Approx(level).margin(1.0));
    }
}

TEST_CASE("RampLevel is linear in perceived brightness", "[actuator][light]") {
    REQUIRE(actuator::RampLevel(0.0f, 80.0f, -10.0f, 1000.0f) == 0.0f);
    REQUIRE(actuator::RampLevel(0.0f, 80.0f, 250.0f, 1000.0f) == Approx(20.0f));
    REQUIRE(actuator::RampLevel(0.0f, 80.0f, 1000.0f, 1000.0f) == 80.0f);
    REQUIRE(actuator::RampLevel(80.0f, 0.0f, 500.0f, 1000.0f) == Approx(40.0f));
    REQUIRE(actuator::RampLevel(10.0f, 90.0f, 0.0f, 0.0f) == 90.0f);
}

TEST_CASE("BreatheLevel is a periodic cosine between lo and hi", "[actuator][light]") {
    REQUIRE(actuator::BreatheLevel(0.0f, 2500.0f, 10.0f, 100.0f) == Approx(10.0f));
    REQUIRE(actuator::BreatheLevel(625.0f, 2500.0f, 10.0f, 100.0f) == Approx(55.0f));
    REQUIRE(actuator::BreatheLevel(1250.0f, 2500.0f, 10.0f, 100.0f) == Approx(100.0f));
    REQUIRE(actuator::BreatheLevel(2500.0f, 2500.0f, 10.0f, 100.0f) == Approx(10.0f));
    REQUIRE(actuator::BreatheLevel(3750.0f, 2500.0f, 10.0f, 100.0f) == Approx(100.0f));
    REQUIRE(actuator::BreatheLevel(-50.0f, 2500.0f, 10.0f, 100.0f) == Approx(10.0f));
    REQUIRE(actuator::BreatheLevel(100.0f, 0.0f, 10.0f, 100.0f) == 100.0f);
}

TEST_CASE("SpeechBrightness follows the speaker level above a floor", "[actuator][light]") {
    REQUIRE(actuator::SpeechBrightness(80.0f, 0.0f, 3.0f, 0.35f) == Approx(28.0f));
    REQUIRE(actuator::SpeechBrightness(80.0f, -1.0f, 3.0f, 0.35f) == Approx(28.0f));
    REQUIRE(actuator::SpeechBrightness(80.0f, 0.1f, 3.0f, 0.35f) ==
            Approx(80.0f * (0.35f + 0.65f * 0.3f)));
    REQUIRE(actuator::SpeechBrightness(80.0f, 0.5f, 3.0f, 0.35f) == Approx(80.0f));  // clipped
    REQUIRE(actuator::SpeechBrightness(80.0f, 1.0f, 3.0f, 0.35f) == Approx(80.0f));
}
