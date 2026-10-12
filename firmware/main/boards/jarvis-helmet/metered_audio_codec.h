// NoAudioCodecSimplex that also measures microphone and speaker levels, so the HUD and
// the eye lights can react to sound without touching the upstream audio code. It also
// probes once at boot which I2S slot carries the microphone data (wiring check), and cuts
// the bass the small speaker cannot play (CONFIG_JARVIS_SPEAKER_HPF_HZ).
#pragma once

#include "codecs/no_audio_codec.h"

#include <atomic>
#include <cstdint>
#include <vector>

// Where ProbeMicrophone() found the microphone data.
enum class MicProbe {
    kNotRun,
    kLeft,      // L/R tied to GND, as wired in docs/hardware
    kRight,     // L/R high or floating: the right slot is used instead
    kNoData,    // SD floats high: nothing drives it (wire, mic power or SCK/WS clocks)
    kStuckLow,  // SD low on both slots: not driven (module pull-down) or shorted to GND
    kError,     // I2S driver error
};

class MeteredAudioCodec : public NoAudioCodecSimplex {
public:
    using NoAudioCodecSimplex::NoAudioCodecSimplex;

    // Digital silence (all-zero blocks), or no fresh data, in the dBFS readings below.
    static constexpr float kSilenceDbfs = -100.0f;

    // Smoothed RMS levels in 0..1 (attack fast, release slow). Safe from any task.
    float GetInputLevel() const { return input_level_.load(std::memory_order_relaxed); }
    float GetOutputLevel() const { return output_level_.load(std::memory_order_relaxed); }

    // The same envelopes in dBFS, unclipped, for diagnostics (self.system.get_status): a
    // microphone that delivers no data reads kSilenceDbfs, a quiet room about -60. A
    // direction without data in the last 300 ms (input off, nothing playing) reads
    // kSilenceDbfs instead of the last block's level.
    float GetInputDbfs() const;
    float GetOutputDbfs() const;

    // Call once before the audio service enables the input (the RX channel is created
    // disabled). Reads ~64 ms of both I2S slots with `din` pulled up, after the INMP441
    // start-up time (blocks ~0.4 s), logs where the microphone data is and receives from
    // that slot from then on (left when nothing is found).
    MicProbe ProbeMicrophone(gpio_num_t din);
    MicProbe mic_probe() const { return mic_probe_.load(std::memory_order_relaxed); }
    static const char* MicProbeName(MicProbe probe);

    // Drives the amplifier's SD_MODE pin from `gpio` (high = on) in step with
    // EnableOutput(). Call once before the audio service starts. GPIO_NUM_NC: no pin.
    void SetAmpEnableGpio(gpio_num_t gpio);
    void EnableOutput(bool enable) override;

protected:
    int Read(int16_t* dest, int samples) override;
    int Write(const int16_t* data, int samples) override;

private:
    // One transposed direct form II biquad section, normalized (a0 = 1).
    struct Biquad {
        float b0 = 1.0f, b1 = 0.0f, b2 = 0.0f, a1 = 0.0f, a2 = 0.0f;
        float z1 = 0.0f, z2 = 0.0f;
    };

    // `data` through the speaker high-pass (4th-order Butterworth, two sections) into
    // hpf_out_. Output task only: Write() has a single caller, so no locking.
    const int16_t* HighPass(const int16_t* data, int samples);

    Biquad hpf_[2];
    bool hpf_ready_ = false;
    std::vector<int16_t> hpf_out_;

    std::atomic<float> input_level_{0.0f};
    std::atomic<float> output_level_{0.0f};
    std::atomic<float> input_dbfs_{kSilenceDbfs};
    std::atomic<float> output_dbfs_{kSilenceDbfs};
    std::atomic<int64_t> input_time_us_{0};
    std::atomic<int64_t> output_time_us_{0};
    std::atomic<MicProbe> mic_probe_{MicProbe::kNotRun};
    gpio_num_t amp_gpio_ = GPIO_NUM_NC;
};
