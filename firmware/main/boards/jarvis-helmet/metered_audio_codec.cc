#include "metered_audio_codec.h"

#include <driver/gpio.h>
#include <driver/i2s_std.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <sdkconfig.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

namespace {

constexpr char TAG[] = "MicProbe";

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

// The dBFS readings fall back to silence when no block came in for this long.
constexpr int64_t kFreshUs = 300 * 1000;

// The INMP441 sends zeros for 2^18 SCK cycles after its clock starts (256 ms at
// 16 kHz x 64 bits per frame); the probe looks at the data after that.
constexpr int kProbeSettleMs = 320;
constexpr int kProbeFrames = 1024;  // 64 ms at 16 kHz
constexpr int kProbeReadTimeoutMs = 200;

// RMS of `samples` values in dBFS (MeteredAudioCodec::kSilenceDbfs for digital silence).
float DbfsFromSumSquares(int64_t sum_squares, int samples) {
    float mean_square = static_cast<float>(sum_squares) / static_cast<float>(samples);
    if (mean_square < 1.0f) {
        return MeteredAudioCodec::kSilenceDbfs;  // digital silence (also avoids log10(0))
    }
    // 20*log10(rms / 32768) == 10*log10(mean_square) - 90.31
    return 10.0f * std::log10(mean_square) - 90.309f;
}

// RMS of one block in dBFS.
float BlockDbfs(const int16_t* data, int samples) {
    int64_t sum_squares = 0;
    for (int i = 0; i < samples; ++i) {
        int32_t s = data[i];
        sum_squares += s * s;
    }
    return DbfsFromSumSquares(sum_squares, samples);
}

// dBFS mapped to 0..1 on the [floor_db, ceil_db] window.
float LevelFromDbfs(float db, float floor_db, float ceil_db) {
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

// NoAudioCodecSimplex's microphone slot (32-bit Philips), with another mode and mask.
i2s_std_slot_config_t MicSlotConfig(i2s_slot_mode_t mode, i2s_std_slot_mask_t mask) {
    i2s_std_slot_config_t cfg = {};
    cfg.data_bit_width = I2S_DATA_BIT_WIDTH_32BIT;
    cfg.slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO;
    cfg.slot_mode = mode;
    cfg.slot_mask = mask;
    cfg.ws_width = I2S_DATA_BIT_WIDTH_32BIT;
    cfg.ws_pol = false;
    cfg.bit_shift = true;
#ifdef I2S_HW_VERSION_2
    cfg.left_align = true;
    cfg.big_endian = false;
    cfg.bit_order_lsb = false;
#endif
    return cfg;
}

// One slot of the probe read. With the data pin pulled up, a slot nobody drives reads
// all ones (-1); the INMP441 drives only its own slot (left when L/R = GND).
struct SlotStats {
    int zeros = 0;  // raw 0: SD low for the whole slot
    int ones = 0;   // raw -1: SD high, the pull-up wins
    float dbfs = MeteredAudioCodec::kSilenceDbfs;  // after NoAudioCodec::Read's >> 12

    bool HasSignal() const { return zeros + ones < kProbeFrames / 2 && dbfs > -95.0f; }
};

// Interleaved stereo frames: left at even indexes, right at odd ones.
SlotStats AnalyzeSlot(const std::vector<int32_t>& frames, int slot) {
    SlotStats stats;
    int64_t sum_squares = 0;
    for (size_t i = slot; i < frames.size(); i += 2) {
        const int32_t raw = frames[i];
        if (raw == 0) {
            ++stats.zeros;
        } else if (raw == -1) {
            ++stats.ones;
        }
        const int32_t value = std::clamp<int32_t>(raw >> 12, -INT16_MAX, INT16_MAX);
        sum_squares += static_cast<int64_t>(value) * value;
    }
    stats.dbfs = DbfsFromSumSquares(sum_squares, kProbeFrames);
    return stats;
}

int Percent(int count) { return count * 100 / kProbeFrames; }

// Smooths a dBFS envelope and stamps it; after a gap (input off, nothing played) the
// envelope restarts from silence instead of the last block of the previous stream.
void SmoothDbfs(std::atomic<float>& dbfs, std::atomic<int64_t>& stamp_us, float target,
                int samples, int sample_rate) {
    const int64_t now = esp_timer_get_time();
    if (now - stamp_us.load(std::memory_order_relaxed) > kFreshUs) {
        dbfs.store(MeteredAudioCodec::kSilenceDbfs, std::memory_order_relaxed);
    }
    Smooth(dbfs, target, samples, sample_rate);
    stamp_us.store(now, std::memory_order_relaxed);
}

}  // namespace

float MeteredAudioCodec::GetInputDbfs() const {
    if (esp_timer_get_time() - input_time_us_.load(std::memory_order_relaxed) > kFreshUs) {
        return kSilenceDbfs;
    }
    return input_dbfs_.load(std::memory_order_relaxed);
}

float MeteredAudioCodec::GetOutputDbfs() const {
    if (esp_timer_get_time() - output_time_us_.load(std::memory_order_relaxed) > kFreshUs) {
        return kSilenceDbfs;
    }
    return output_dbfs_.load(std::memory_order_relaxed);
}

const char* MeteredAudioCodec::MicProbeName(MicProbe probe) {
    switch (probe) {
        case MicProbe::kLeft:
            return "left";
        case MicProbe::kRight:
            return "right";
        case MicProbe::kNoData:
            return "no_data";
        case MicProbe::kStuckLow:
            return "stuck_low";
        case MicProbe::kError:
            return "error";
        case MicProbe::kNotRun:
            break;
    }
    return "not_run";
}

MicProbe MeteredAudioCodec::ProbeMicrophone(gpio_num_t din) {
    std::vector<int32_t> frames(kProbeFrames * 2);
    const size_t want = frames.size() * sizeof(int32_t);
    const i2s_std_slot_config_t stereo = MicSlotConfig(I2S_SLOT_MODE_STEREO, I2S_STD_SLOT_BOTH);
    bool read_ok = rx_handle_ != nullptr &&
                   i2s_channel_reconfig_std_slot(rx_handle_, &stereo) == ESP_OK;
    if (read_ok) {
        gpio_pullup_en(din);
        read_ok = i2s_channel_enable(rx_handle_) == ESP_OK;
        if (read_ok) {
            size_t got = 0;
            const int64_t settled = esp_timer_get_time() + kProbeSettleMs * 1000;
            while (read_ok && esp_timer_get_time() < settled) {
                read_ok = i2s_channel_read(rx_handle_, frames.data(), want, &got,
                                           kProbeReadTimeoutMs) == ESP_OK;
            }
            read_ok = read_ok &&
                      i2s_channel_read(rx_handle_, frames.data(), want, &got,
                                       kProbeReadTimeoutMs) == ESP_OK &&
                      got == want;
            i2s_channel_disable(rx_handle_);
        }
        gpio_pullup_dis(din);
    }

    MicProbe result = MicProbe::kError;
    SlotStats left;
    SlotStats right;
    if (read_ok) {
        left = AnalyzeSlot(frames, 0);
        right = AnalyzeSlot(frames, 1);
        if (left.HasSignal()) {
            result = MicProbe::kLeft;
        } else if (right.HasSignal()) {
            result = MicProbe::kRight;
        } else if (left.zeros + right.zeros > left.ones + right.ones) {
            result = MicProbe::kStuckLow;
        } else {
            result = MicProbe::kNoData;
        }
    }

    // Receive from the slot that carries the data from now on (left when none does). The
    // channel stays disabled: the audio service enables it.
    const i2s_std_slot_config_t mono = MicSlotConfig(
        I2S_SLOT_MODE_MONO, result == MicProbe::kRight ? I2S_STD_SLOT_RIGHT : I2S_STD_SLOT_LEFT);
    if (rx_handle_ == nullptr || i2s_channel_reconfig_std_slot(rx_handle_, &mono) != ESP_OK) {
        result = MicProbe::kError;
    }
    mic_probe_.store(result, std::memory_order_relaxed);

    if (result != MicProbe::kError) {
        ESP_LOGI(TAG, "Slots with SD (GPIO%d) pulled up: left zeros %d%% ones %d%% %.1f dBFS, "
                      "right zeros %d%% ones %d%% %.1f dBFS",
                 static_cast<int>(din), Percent(left.zeros), Percent(left.ones),
                 static_cast<double>(left.dbfs), Percent(right.zeros), Percent(right.ones),
                 static_cast<double>(right.dbfs));
    }
    switch (result) {
        case MicProbe::kLeft:
            ESP_LOGI(TAG, "Microphone on the left slot");
            break;
        case MicProbe::kRight:
            ESP_LOGW(TAG, "Microphone data on the RIGHT slot: tie the INMP441 L/R pin to GND. "
                          "Using the right slot for now");
            break;
        case MicProbe::kNoData:
            ESP_LOGE(TAG, "No microphone data: nothing drives SD. Check the INMP441 power "
                          "(VDD 3V3, GND), its SD wire and the SCK/WS wires (not swapped)");
            break;
        case MicProbe::kStuckLow:
            // INMP441 modules often carry a 100k pull-down on SD, which beats the ~45k
            // internal pull-up: an undriven line reads low too.
            ESP_LOGE(TAG, "No microphone data: SD stays low on both slots. The microphone "
                          "does not drive it (check VDD 3V3, GND, the SCK/WS wires, not "
                          "swapped) or SD is shorted to GND");
            break;
        case MicProbe::kError:
        case MicProbe::kNotRun:
            ESP_LOGE(TAG, "Probe failed: I2S RX channel error");
            break;
    }
    return result;
}

void MeteredAudioCodec::SetAmpEnableGpio(gpio_num_t gpio) {
    amp_gpio_ = gpio;
    if (gpio == GPIO_NUM_NC) {
        return;
    }
    gpio_config_t config = {};
    config.pin_bit_mask = 1ULL << gpio;
    config.mode = GPIO_MODE_OUTPUT;
    gpio_config(&config);
    gpio_set_level(gpio, output_enabled() ? 1 : 0);
}

void MeteredAudioCodec::EnableOutput(bool enable) {
    // The amp goes off before the I2S clocks stop and on after they run again, so it
    // never sees a half-stopped clock (the MAX98357A pops on that).
    if (!enable && amp_gpio_ != GPIO_NUM_NC) {
        gpio_set_level(amp_gpio_, 0);
    }
    NoAudioCodecSimplex::EnableOutput(enable);
    if (enable && amp_gpio_ != GPIO_NUM_NC) {
        gpio_set_level(amp_gpio_, 1);
    }
}

int MeteredAudioCodec::Read(int16_t* dest, int samples) {
    int n = NoAudioCodecSimplex::Read(dest, samples);
    if (n > 0) {
        const float db = BlockDbfs(dest, n);
        const int rate = input_sample_rate_ * input_channels_;
        Smooth(input_level_, LevelFromDbfs(db, kInputFloorDb, kInputCeilDb), n, rate);
        SmoothDbfs(input_dbfs_, input_time_us_, db, n, rate);
    }
    return n;
}

int MeteredAudioCodec::Write(const int16_t* data, int samples) {
    if (samples > 0) {
        // Measured on the decoded PCM before NoAudioCodec applies the output volume, so the
        // HUD follows the speech envelope even at low volume.
        const float db = BlockDbfs(data, samples);
        const int rate = output_sample_rate_ * output_channels_;
        Smooth(output_level_, LevelFromDbfs(db, kOutputFloorDb, kOutputCeilDb), samples, rate);
        SmoothDbfs(output_dbfs_, output_time_us_, db, samples, rate);
#if CONFIG_JARVIS_SPEAKER_HPF_HZ > 0
        if (output_channels_ == 1) {  // the filter state is per channel: mono only
            return NoAudioCodecSimplex::Write(HighPass(data, samples), samples);
        }
#endif
    }
    return NoAudioCodecSimplex::Write(data, samples);
}

const int16_t* MeteredAudioCodec::HighPass(const int16_t* data, int samples) {
    if (!hpf_ready_) {
        // RBJ audio EQ cookbook high-pass; the two Q values make a 4th-order Butterworth.
        constexpr float kPi = 3.14159265f;
        constexpr float kSectionQ[2] = {0.54119610f, 1.30656296f};
        const float w0 = 2.0f * kPi * static_cast<float>(CONFIG_JARVIS_SPEAKER_HPF_HZ) /
                         static_cast<float>(output_sample_rate_);
        const float cos_w0 = std::cos(w0);
        for (int i = 0; i < 2; ++i) {
            const float alpha = std::sin(w0) / (2.0f * kSectionQ[i]);
            const float a0 = 1.0f + alpha;
            Biquad& s = hpf_[i];
            s.b0 = (1.0f + cos_w0) / 2.0f / a0;
            s.b1 = -(1.0f + cos_w0) / a0;
            s.b2 = s.b0;
            s.a1 = -2.0f * cos_w0 / a0;
            s.a2 = (1.0f - alpha) / a0;
        }
        hpf_ready_ = true;
    }
    hpf_out_.resize(samples);
    for (int i = 0; i < samples; ++i) {
        float y = static_cast<float>(data[i]);
        for (Biquad& s : hpf_) {
            const float x = y;
            y = s.b0 * x + s.z1;
            s.z1 = s.b1 * x - s.a1 * y + s.z2;
            s.z2 = s.b2 * x - s.a2 * y;
        }
        hpf_out_[i] = static_cast<int16_t>(std::clamp(std::lround(y), -32768L, 32767L));
    }
    // During silence the state decays towards denormals, which the FPU handles slowly.
    constexpr float kFlush = 1e-15f;
    for (Biquad& s : hpf_) {
        if (std::fabs(s.z1) < kFlush) {
            s.z1 = 0.0f;
        }
        if (std::fabs(s.z2) < kFlush) {
            s.z2 = 0.0f;
        }
    }
    return hpf_out_.data();
}
