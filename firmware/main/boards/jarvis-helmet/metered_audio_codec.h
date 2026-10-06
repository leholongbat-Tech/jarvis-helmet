// NoAudioCodecSimplex that also measures microphone and speaker levels, so the HUD and
// the eye lights can react to sound without touching the upstream audio code.
#pragma once

#include "codecs/no_audio_codec.h"

#include <atomic>

class MeteredAudioCodec : public NoAudioCodecSimplex {
public:
    using NoAudioCodecSimplex::NoAudioCodecSimplex;

    // Smoothed RMS levels in 0..1 (attack fast, release slow). Safe from any task.
    float GetInputLevel() const { return input_level_.load(std::memory_order_relaxed); }
    float GetOutputLevel() const { return output_level_.load(std::memory_order_relaxed); }

protected:
    int Read(int16_t* dest, int samples) override;
    int Write(const int16_t* data, int samples) override;

private:
    std::atomic<float> input_level_{0.0f};
    std::atomic<float> output_level_{0.0f};
};
