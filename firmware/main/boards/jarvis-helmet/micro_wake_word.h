// microWakeWord detector for "Quản gia" (Jarvis Helmet plan B2, CONFIG_JARVIS_MICRO_WAKE_WORD).
//
// Pipeline: 16 kHz mono int16 audio (the AFE output WakeNet/MultiNet see) -> 40 log-mel
// features per 30 ms window every 10 ms (esphome/esp-micro-speech-features) -> streaming
// int8 TFLite Micro model (espressif/esp-tflite-micro, tensor arena in PSRAM, size probed)
// -> mean of the last CONFIG_JARVIS_MWW_SLIDING_WINDOW probabilities compared against
// CONFIG_JARVIS_MWW_CUTOFF_PCT -> detection, then a refractory period.
//
// The model is compiled in from mww_model_data.c (tools/wakeword/export/tflite_to_c.py).
// With the committed size-0 placeholder Initialize() logs a warning and returns false, so
// the owner simply drops the detector.
//
// Threading: FeedMono() is called by a single producer (the AFE fetch task) and never
// blocks; features + inference run in an own task ("mww"). A detection is delivered from
// the next FeedMono() call, i.e. in the producer's task, like WakeNet/MultiNet results.
//
// Own implementation on Apache-2.0 libraries; no ESPHome (GPLv3) code is used.
#ifndef JARVIS_MICRO_WAKE_WORD_H
#define JARVIS_MICRO_WAKE_WORD_H

#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "wake_word.h"

class MicroWakeWord : public WakeWord {
public:
    MicroWakeWord();
    ~MicroWakeWord() override;

    MicroWakeWord(const MicroWakeWord&) = delete;
    MicroWakeWord& operator=(const MicroWakeWord&) = delete;

    // models_list is unused: the model is linked into the firmware.
    bool Initialize(AudioCodec* codec, srmodel_list_t* models_list) override;
    // Interleaved codec samples; channel 0 is used.
    void Feed(const std::vector<int16_t>& data) override;
    void OnWakeWordDetected(std::function<void(const std::string& wake_word)> callback) override;
    // Start() also resets the feature/probability state (1 s warm-up before detecting).
    void Start() override;
    void Stop() override;
    size_t GetFeedSize() override;
    // Wake word audio upload uses the AFE engine's shared cache.
    void EncodeWakeWordData() override {}
    bool GetWakeWordOpus(std::vector<uint8_t>& /*opus*/) override { return false; }
    const std::string& GetLastDetectedWakeWord() const override;

    // Queues 16 kHz mono samples for the inference task (drops them when the queue is
    // full). If a detection is pending, the callback runs here instead and true is returned.
    bool FeedMono(const int16_t* data, size_t samples);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

#endif  // JARVIS_MICRO_WAKE_WORD_H
