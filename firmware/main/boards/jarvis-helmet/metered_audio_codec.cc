#include "metered_audio_codec.h"

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace {

// dBFS window mapped linearly onto 0..1. The microphone path is NoAudioCodec::Read's 32-bit
// sample >> 12 (24 dB above the top 16 bits), so its idle noise sits around -60 dBFS and
// close speech peaks near -10 dBFS. Decoded TTS (before volume scaling) has speech RMS
// around -25..-12 dBFS.
constexpr float kInputFloorDb = -58.0f;
constexpr float kInputCeilDb = -14.0f;
constexpr float kOutputFloorDb = -50.0f;
constexpr float kOutputCeilDb = -12.0f;

// Envelope time constants: rise quickly so syllables register, fall slowly so animations
// do not flicker between words.
constexpr float kAttackMs = 25.0f;
constexpr float kReleaseMs = 300.0f;

// RMS of one block mapped to 0..1 on the [floor_db, ceil_db] window.
float BlockLevel(const int16_t* data, int samples, float floor_db, float ceil_db) {
    int64_t sum_squares = 0;
    for (int i = 0; i < samples; ++i) {
        int32_t s = data[i];
        sum_squares += s * s;
    }
    float mean_square = static_cast<float>(sum_squares) / static_cast<float>(samples);
    if (mean_square < 1.0f) {
        return 0.0f;  // digital silence (also avoids log10(0))
    }
    // 20*log10(rms / 32768) == 10*log10(mean_square) - 90.31
    float db = 10.0f * std::log10(mean_square) - 90.309f;
    return std::clamp((db - floor_db) / (ceil_db - floor_db), 0.0f, 1.0f);
}

// One-pole smoothing with separate attack/release time constants. Each atomic has a single
// writer (the audio input task for Read, the audio output task for Write), so a plain
// load/store pair is race-free; readers only ever see complete float values.
void Smooth(std::atomic<float>& level, float target, int samples, int sample_rate) {
    if (sample_rate <= 0) {
        return;
    }
    float block_ms = 1000.0f * static_cast<float>(samples) / static_cast<float>(sample_rate);
    float current = level.load(std::memory_order_relaxed);
    float tau = target > current ? kAttackMs : kReleaseMs;
    float alpha = 1.0f - std::exp(-block_ms / tau);
    level.store(current + alpha * (target - current), std::memory_order_relaxed);
}

}  // namespace

int MeteredAudioCodec::Read(int16_t* dest, int samples) {
    int n = NoAudioCodecSimplex::Read(dest, samples);
    if (n > 0) {
        Smooth(input_level_, BlockLevel(dest, n, kInputFloorDb, kInputCeilDb), n,
               input_sample_rate_ * input_channels_);
    }
    return n;
}

int MeteredAudioCodec::Write(const int16_t* data, int samples) {
    if (samples > 0) {
        // Measured on the decoded PCM before NoAudioCodec applies the output volume, so the
        // HUD follows the speech envelope even at low volume.
        Smooth(output_level_, BlockLevel(data, samples, kOutputFloorDb, kOutputCeilDb), samples,
               output_sample_rate_ * output_channels_);
    }
    return NoAudioCodecSimplex::Write(data, samples);
}
