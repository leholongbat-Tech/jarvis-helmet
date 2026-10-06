// microWakeWord detector, see micro_wake_word.h.
//
// Compiled only with CONFIG_JARVIS_MICRO_WAKE_WORD: the esp-tflite-micro, esp-nn and
// esp-micro-speech-features components are added to the build (main/idf_component.yml)
// only in that case, so with the option off this file is an empty translation unit.
#include <sdkconfig.h>

#ifdef CONFIG_JARVIS_MICRO_WAKE_WORD

#include "micro_wake_word.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <iterator>
#include <optional>

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/stream_buffer.h>
#include <freertos/task.h>

#include <frontend.h>
#include <frontend_util.h>

#include <tensorflow/lite/micro/micro_allocator.h>
#include <tensorflow/lite/micro/micro_interpreter.h>
#include <tensorflow/lite/micro/micro_mutable_op_resolver.h>
#include <tensorflow/lite/micro/micro_resource_variable.h>
#include <tensorflow/lite/schema/schema_generated.h>

#include "audio_codec.h"

#define TAG "MicroWakeWord"

// Defaults match boards/jarvis-helmet/Kconfig.jarvis.
#ifndef CONFIG_JARVIS_MWW_CUTOFF_PCT
#define CONFIG_JARVIS_MWW_CUTOFF_PCT 97
#endif
#ifndef CONFIG_JARVIS_MWW_SLIDING_WINDOW
#define CONFIG_JARVIS_MWW_SLIDING_WINDOW 5
#endif

// Model blob, generated into mww_model_data.c by tools/wakeword/export/tflite_to_c.py.
extern "C" {
extern const unsigned char g_jarvis_mww_model[];
extern const unsigned int g_jarvis_mww_model_size;
extern const unsigned int g_jarvis_mww_tensor_arena_size;
}

namespace {

// Feature frontend. Must match the training pipeline (microWakeWord/pymicro-features):
// 40 channels, 30 ms window, 10 ms step, 125-7500 Hz, noise reduction, PCAN, log scale.
constexpr int kSampleRate = 16000;
constexpr int kFeatureCount = 40;
constexpr size_t kWindowMs = 30;
constexpr size_t kStepMs = 10;
constexpr size_t kStepSamples = kSampleRate / 1000 * kStepMs;
constexpr size_t kStepBytes = kStepSamples * sizeof(int16_t);
// Training feeds the model feature * 0.0390625 (= 1/25.6) as the float input.
constexpr float kFeatureToFloat = 0.0390625f;

// Decision.
constexpr int kMaxSlidingWindow = 20;
constexpr int kSlidingWindow = CONFIG_JARVIS_MWW_SLIDING_WINDOW;
constexpr unsigned kCutoff = CONFIG_JARVIS_MWW_CUTOFF_PCT * 255 / 100;  // 0..255 domain
constexpr int kRefractoryMs = 1000;
constexpr int kRefractorySlices = kRefractoryMs / static_cast<int>(kStepMs);
static_assert(kSlidingWindow >= 1 && kSlidingWindow <= kMaxSlidingWindow,
              "CONFIG_JARVIS_MWW_SLIDING_WINDOW out of range");
static_assert(CONFIG_JARVIS_MWW_CUTOFF_PCT >= 0 && CONFIG_JARVIS_MWW_CUTOFF_PCT <= 100,
              "CONFIG_JARVIS_MWW_CUTOFF_PCT out of range");

// Memory and task.
constexpr size_t kAudioQueueBytes = kSampleRate * sizeof(int16_t) * 300 / 1000;  // 300 ms
constexpr size_t kDefaultArenaBytes = 32 * 1024;
constexpr size_t kMaxArenaBytes = 256 * 1024;
constexpr int kArenaAttempts = 4;
constexpr size_t kVariableArenaBytes = 2048;
constexpr int kMaxResourceVariables = 20;
constexpr uint32_t kTaskStackBytes = 6 * 1024;
constexpr UBaseType_t kTaskPriority = 2;  // below audio_afe (3): never delays the AFE fetch
constexpr int64_t kStatsPeriodUs = 10 * 1000 * 1000;

// Reported wake word (what the server receives): "Quản gia", NFC UTF-8.
constexpr char kWakeWordText[] = "Qu\xe1\xba\xa3n gia";

// Operators used by the streaming microWakeWord (MixedNet) models.
constexpr unsigned kOpCount = 20;
using OpResolver = tflite::MicroMutableOpResolver<kOpCount>;

constexpr size_t AlignUp16(size_t value) { return (value + 15) & ~static_cast<size_t>(15); }

enum class ArenaResult { kOk, kTooSmall, kNoMemory };

}  // namespace

struct MicroWakeWord::Impl {
    AudioCodec* codec = nullptr;
    std::function<void(const std::string&)> callback;
    const std::string wake_word{kWakeWordText};

    // Shared between the producer (FeedMono), the control calls and the inference task.
    std::atomic<bool> ready{false};
    std::atomic<bool> running{false};
    std::atomic<bool> reset_pending{false};
    std::atomic<bool> detected{false};
    std::atomic<bool> exit_requested{false};
    std::atomic<bool> task_parked{false};
    std::atomic<uint32_t> dropped_bytes{0};

    // Audio hand-off: single writer (FeedMono), single reader (inference task).
    StreamBufferHandle_t queue = nullptr;
    StaticStreamBuffer_t* queue_struct = nullptr;  // internal RAM
    uint8_t* queue_storage = nullptr;              // PSRAM
    TaskHandle_t task = nullptr;
    StaticTask_t* task_tcb = nullptr;  // internal RAM
    StackType_t* task_stack = nullptr; // PSRAM, like the upstream audio_afe task

    FrontendState frontend = {};
    bool frontend_ready = false;

    const tflite::Model* model = nullptr;
    OpResolver resolver;
    uint8_t* arena = nullptr;           // PSRAM
    size_t arena_bytes = 0;
    uint8_t* variable_arena = nullptr;  // PSRAM, holds the resource variable table
    std::optional<tflite::MicroInterpreter> interpreter;
    TfLiteTensor* input = nullptr;
    TfLiteTensor* output = nullptr;
    int stride = 1;                     // feature slices per inference (model input dim 1)
    int64_t quant_multiplier_q16 = 0;   // uint16 feature -> int8 input, from the model params
    int32_t quant_zero_point = 0;

    // Streaming state, owned by the inference task.
    int stride_step = 0;
    uint8_t probabilities[kMaxSlidingWindow] = {};
    int probability_index = 0;
    uint8_t newest_probability = 0;
    int cooldown_slices = kRefractorySlices;
    bool invoke_error_logged = false;

    // Statistics, owned by the inference task.
    uint32_t stats_invokes = 0;
    int64_t stats_invoke_us = 0;
    uint8_t stats_peak = 0;
    int64_t stats_since_us = 0;
    bool stats_first_report = true;

    bool LoadModel();
    ArenaResult TryAllocate(size_t bytes);
    bool CheckTensors();
    void FreeModel();
    bool SetupFrontend();
    bool StartTask();
    void Release();

    static void TaskEntry(void* arg);
    void Run();
    void ResetStreamingState();
    void ProcessAudio(const int16_t* samples, size_t count);
    void ProcessFeatures(const uint16_t* features);
    void Decide();
    void ReportStatistics();
    int8_t Quantize(uint16_t feature) const;
};

bool MicroWakeWord::Impl::LoadModel() {
    if (g_jarvis_mww_model_size < 16 || !tflite::ModelBufferHasIdentifier(g_jarvis_mww_model)) {
        ESP_LOGE(TAG, "Model blob is not a TFLite flatbuffer");
        return false;
    }
    model = tflite::GetModel(g_jarvis_mww_model);
    if (model->version() != TFLITE_SCHEMA_VERSION) {
        ESP_LOGE(TAG, "Model schema %u, expected %d", static_cast<unsigned>(model->version()),
                 TFLITE_SCHEMA_VERSION);
        return false;
    }

    const bool ops_ok =
        resolver.AddCallOnce() == kTfLiteOk && resolver.AddVarHandle() == kTfLiteOk &&
        resolver.AddReshape() == kTfLiteOk && resolver.AddReadVariable() == kTfLiteOk &&
        resolver.AddStridedSlice() == kTfLiteOk && resolver.AddConcatenation() == kTfLiteOk &&
        resolver.AddAssignVariable() == kTfLiteOk && resolver.AddConv2D() == kTfLiteOk &&
        resolver.AddMul() == kTfLiteOk && resolver.AddAdd() == kTfLiteOk &&
        resolver.AddMean() == kTfLiteOk && resolver.AddFullyConnected() == kTfLiteOk &&
        resolver.AddLogistic() == kTfLiteOk && resolver.AddQuantize() == kTfLiteOk &&
        resolver.AddDepthwiseConv2D() == kTfLiteOk && resolver.AddAveragePool2D() == kTfLiteOk &&
        resolver.AddMaxPool2D() == kTfLiteOk && resolver.AddPad() == kTfLiteOk &&
        resolver.AddPack() == kTfLiteOk && resolver.AddSplitV() == kTfLiteOk;
    if (!ops_ok) {
        ESP_LOGE(TAG, "Failed to register the model operators");
        return false;
    }

    // The manifest arena size is only a hint: kernels and esp-nn versions differ, so grow
    // the arena until AllocateTensors() succeeds.
    size_t bytes = g_jarvis_mww_tensor_arena_size > 0 ? AlignUp16(g_jarvis_mww_tensor_arena_size)
                                                       : kDefaultArenaBytes;
    for (int attempt = 0; attempt < kArenaAttempts && bytes <= kMaxArenaBytes; ++attempt) {
        const ArenaResult result = TryAllocate(bytes);
        if (result == ArenaResult::kOk) {
            ESP_LOGI(TAG, "Tensor arena %u B in PSRAM, %u B used", static_cast<unsigned>(bytes),
                     static_cast<unsigned>(interpreter->arena_used_bytes()));
            return CheckTensors();
        }
        FreeModel();
        if (result == ArenaResult::kNoMemory) {
            return false;
        }
        ESP_LOGW(TAG, "Tensor arena of %u B is too small", static_cast<unsigned>(bytes));
        bytes = AlignUp16(bytes + bytes / 2);
    }
    ESP_LOGE(TAG, "Could not allocate the tensors (gave up below %u B)",
             static_cast<unsigned>(bytes));
    return false;
}

ArenaResult MicroWakeWord::Impl::TryAllocate(size_t bytes) {
    arena = static_cast<uint8_t*>(
        heap_caps_aligned_alloc(16, bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    variable_arena = static_cast<uint8_t*>(
        heap_caps_aligned_alloc(16, kVariableArenaBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (arena == nullptr || variable_arena == nullptr) {
        ESP_LOGE(TAG, "PSRAM allocation of %u B failed", static_cast<unsigned>(bytes));
        return ArenaResult::kNoMemory;
    }
    // The streaming state lives in resource variables; their table gets its own small arena,
    // their buffers come from the tensor arena.
    tflite::MicroAllocator* variable_allocator =
        tflite::MicroAllocator::Create(variable_arena, kVariableArenaBytes);
    tflite::MicroResourceVariables* variables =
        variable_allocator == nullptr
            ? nullptr
            : tflite::MicroResourceVariables::Create(variable_allocator, kMaxResourceVariables);
    if (variables == nullptr) {
        ESP_LOGE(TAG, "Failed to create the resource variables");
        return ArenaResult::kNoMemory;
    }
    interpreter.emplace(model, resolver, arena, bytes, variables);
    if (interpreter->initialization_status() != kTfLiteOk ||
        interpreter->AllocateTensors() != kTfLiteOk) {
        return ArenaResult::kTooSmall;
    }
    arena_bytes = bytes;
    return ArenaResult::kOk;
}

bool MicroWakeWord::Impl::CheckTensors() {
    input = interpreter->input(0);
    output = interpreter->output(0);
    if (input == nullptr || output == nullptr) {
        ESP_LOGE(TAG, "Model has no input/output tensor");
        return false;
    }
    if (input->type != kTfLiteInt8 || input->dims->size != 3 || input->dims->data[0] != 1 ||
        input->dims->data[1] < 1 || input->dims->data[2] != kFeatureCount) {
        ESP_LOGE(TAG, "Unexpected input tensor, need int8 [1, n, %d]", kFeatureCount);
        return false;
    }
    if (output->type != kTfLiteUInt8 || output->dims->size != 2 || output->dims->data[0] != 1 ||
        output->dims->data[1] != 1) {
        ESP_LOGE(TAG, "Unexpected output tensor, need uint8 [1, 1]");
        return false;
    }
    stride = input->dims->data[1];

    // q = feature * 0.0390625 / scale + zero_point, as Q16 fixed point so the inference task
    // stays integer-only. Taken from the model's own input parameters (typically scale
    // 26/255, zero point -128) rather than a fixed feature * 256 / 666 - 128 mapping.
    const float scale = input->params.scale;
    if (!(scale > 0.0f)) {
        ESP_LOGE(TAG, "Input tensor is not quantized");
        return false;
    }
    // Double precision, same as tools/wakeword/eval_wakeword.py, so both round identically.
    quant_multiplier_q16 =
        std::lround(static_cast<double>(kFeatureToFloat) / static_cast<double>(scale) * 65536.0);
    quant_zero_point = input->params.zero_point;
    return true;
}

void MicroWakeWord::Impl::FreeModel() {
    input = nullptr;
    output = nullptr;
    interpreter.reset();  // before the arenas it points into
    heap_caps_free(arena);
    arena = nullptr;
    heap_caps_free(variable_arena);
    variable_arena = nullptr;
    arena_bytes = 0;
}

bool MicroWakeWord::Impl::SetupFrontend() {
    FrontendConfig config;
    FrontendFillConfigWithDefaults(&config);
    config.window.size_ms = kWindowMs;
    config.window.step_size_ms = kStepMs;
    config.filterbank.num_channels = kFeatureCount;
    config.filterbank.lower_band_limit = 125.0f;
    config.filterbank.upper_band_limit = 7500.0f;
    config.noise_reduction.smoothing_bits = 10;
    config.noise_reduction.even_smoothing = 0.025f;
    config.noise_reduction.odd_smoothing = 0.06f;
    config.noise_reduction.min_signal_remaining = 0.05f;
    config.pcan_gain_control.enable_pcan = 1;
    config.pcan_gain_control.strength = 0.95f;
    config.pcan_gain_control.offset = 80.0f;
    config.pcan_gain_control.gain_bits = 21;
    config.log_scale.enable_log = 1;
    config.log_scale.scale_shift = 6;
    // Table setup uses floating point: done here, the inference task stays integer-only.
    if (!FrontendPopulateState(&config, &frontend, kSampleRate)) {
        ESP_LOGE(TAG, "Failed to set up the feature frontend");
        return false;
    }
    frontend_ready = true;
    return true;
}

bool MicroWakeWord::Impl::StartTask() {
    queue_struct = static_cast<StaticStreamBuffer_t*>(
        heap_caps_malloc(sizeof(StaticStreamBuffer_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    queue_storage = static_cast<uint8_t*>(
        heap_caps_malloc(kAudioQueueBytes + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    task_tcb = static_cast<StaticTask_t*>(
        heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    task_stack = static_cast<StackType_t*>(
        heap_caps_malloc(kTaskStackBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (queue_struct == nullptr || queue_storage == nullptr || task_tcb == nullptr ||
        task_stack == nullptr) {
        ESP_LOGE(TAG, "Failed to allocate the inference task memory");
        return false;
    }
    queue = xStreamBufferCreateStatic(kAudioQueueBytes, kStepBytes, queue_storage, queue_struct);
    if (queue == nullptr) {
        ESP_LOGE(TAG, "Failed to create the audio queue");
        return false;
    }
    task = xTaskCreateStatic(&Impl::TaskEntry, "mww", kTaskStackBytes, this, kTaskPriority,
                             task_stack, task_tcb);
    if (task == nullptr) {
        ESP_LOGE(TAG, "Failed to create the inference task");
        return false;
    }
    return true;
}

void MicroWakeWord::Impl::Release() {
    ready = false;
    running = false;
    if (task != nullptr) {
        // Let the task park itself between two queue reads, then delete it while it is
        // suspended so its static TCB/stack can be freed safely.
        exit_requested = true;
        for (int i = 0; i < 100 && !task_parked.load(); ++i) {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        vTaskDelete(task);
        task = nullptr;
    }
    if (queue != nullptr) {
        vStreamBufferDelete(queue);
        queue = nullptr;
    }
    heap_caps_free(queue_storage);
    queue_storage = nullptr;
    heap_caps_free(queue_struct);
    queue_struct = nullptr;
    heap_caps_free(task_stack);
    task_stack = nullptr;
    heap_caps_free(task_tcb);
    task_tcb = nullptr;
    if (frontend_ready) {
        FrontendFreeStateContents(&frontend);
        frontend_ready = false;
    }
    FreeModel();
}

void MicroWakeWord::Impl::TaskEntry(void* arg) {
    auto* self = static_cast<Impl*>(arg);
    self->Run();
    self->task_parked = true;
    vTaskSuspend(nullptr);  // deleted by Release()
}

void MicroWakeWord::Impl::Run() {
    int16_t samples[kStepSamples];
    size_t filled = 0;  // bytes
    stats_since_us = esp_timer_get_time();
    while (!exit_requested.load()) {
        const size_t received = xStreamBufferReceive(
            queue, reinterpret_cast<uint8_t*>(samples) + filled, kStepBytes - filled,
            pdMS_TO_TICKS(100));
        if (reset_pending.exchange(false)) {
            ResetStreamingState();
            filled = 0;
            continue;
        }
        if (received == 0) {
            continue;
        }
        if (!running.load()) {
            filled = 0;
            continue;
        }
        filled += received;
        if (filled < kStepBytes) {
            continue;
        }
        filled = 0;
        ProcessAudio(samples, kStepSamples);
        ReportStatistics();
    }
}

void MicroWakeWord::Impl::ResetStreamingState() {
    // Drop audio queued before Start(). Reset is allowed here: the writer never blocks and
    // this task, the only reader, is not waiting on the queue.
    if (xStreamBufferReset(queue) != pdPASS) {
        uint8_t scratch[64];
        while (xStreamBufferReceive(queue, scratch, sizeof(scratch), 0) > 0) {
        }
    }
    FrontendReset(&frontend);
    stride_step = 0;
    std::fill(std::begin(probabilities), std::end(probabilities), 0);
    probability_index = 0;
    newest_probability = 0;
    // Warm-up: lets the noise estimate settle and flushes the model's streaming state.
    cooldown_slices = kRefractorySlices;
}

void MicroWakeWord::Impl::ProcessAudio(const int16_t* samples, size_t count) {
    while (count > 0) {
        size_t used = 0;
        const FrontendOutput features = FrontendProcessSamples(&frontend, samples, count, &used);
        if (features.size == static_cast<size_t>(kFeatureCount)) {
            ProcessFeatures(features.values);
        }
        if (used == 0) {
            break;
        }
        samples += used;
        count -= used;
    }
}

int8_t MicroWakeWord::Impl::Quantize(uint16_t feature) const {
    const int64_t value =
        ((static_cast<int64_t>(feature) * quant_multiplier_q16 + 32768) >> 16) + quant_zero_point;
    return static_cast<int8_t>(std::clamp<int64_t>(value, INT8_MIN, INT8_MAX));
}

void MicroWakeWord::Impl::ProcessFeatures(const uint16_t* features) {
    // Fill one row of the [1, stride, 40] input; run the model once all rows are new.
    int8_t* row = input->data.int8 + stride_step * kFeatureCount;
    for (int i = 0; i < kFeatureCount; ++i) {
        row[i] = Quantize(features[i]);
    }
    bool fresh = false;
    if (++stride_step >= stride) {
        stride_step = 0;
        const int64_t start = esp_timer_get_time();
        const TfLiteStatus status = interpreter->Invoke();
        stats_invoke_us += esp_timer_get_time() - start;
        ++stats_invokes;
        if (status != kTfLiteOk) {
            if (!invoke_error_logged) {
                ESP_LOGE(TAG, "Invoke failed: %d", static_cast<int>(status));
                invoke_error_logged = true;
            }
            return;
        }
        newest_probability = output->data.uint8[0];
        probabilities[probability_index] = newest_probability;
        probability_index = (probability_index + 1) % kSlidingWindow;
        stats_peak = std::max(stats_peak, newest_probability);
        fresh = true;
    }
    // Refractory period: count 10 ms slices whose latest probability is below the cutoff, so
    // one long utterance cannot trigger twice.
    if (cooldown_slices > 0 && static_cast<unsigned>(newest_probability) < kCutoff) {
        --cooldown_slices;
    }
    if (fresh) {
        Decide();
    }
}

void MicroWakeWord::Impl::Decide() {
    if (cooldown_slices > 0) {
        return;
    }
    unsigned sum = 0;
    for (int i = 0; i < kSlidingWindow; ++i) {
        sum += probabilities[i];
    }
    if (sum <= kCutoff * kSlidingWindow) {
        return;
    }
    ESP_LOGI(TAG, "Detected \"%s\" (mean %u/255, cutoff %u, window %d)", wake_word.c_str(),
             sum / kSlidingWindow, kCutoff, kSlidingWindow);
    std::fill(std::begin(probabilities), std::end(probabilities), 0);
    newest_probability = 0;
    cooldown_slices = kRefractorySlices;
    detected = true;
}

void MicroWakeWord::Impl::ReportStatistics() {
    const int64_t now = esp_timer_get_time();
    if (now - stats_since_us < kStatsPeriodUs) {
        return;
    }
    const uint32_t dropped = dropped_bytes.exchange(0);
    if (dropped > 0) {
        ESP_LOGW(TAG, "Dropped %u B of audio (inference too slow?)", static_cast<unsigned>(dropped));
    }
    if (stats_invokes > 0) {
        const unsigned invokes = static_cast<unsigned>(stats_invokes);
        const unsigned average_us = static_cast<unsigned>(stats_invoke_us / stats_invokes);
        const unsigned peak = stats_peak;
        // First report at INFO for the CPU budget, later ones at DEBUG (threshold tuning).
        if (stats_first_report) {
            ESP_LOGI(TAG, "%u inferences, avg %u us, peak probability %u/255", invokes,
                     average_us, peak);
            stats_first_report = false;
        } else {
            ESP_LOGD(TAG, "%u inferences, avg %u us, peak probability %u/255", invokes,
                     average_us, peak);
        }
    }
    stats_invokes = 0;
    stats_invoke_us = 0;
    stats_peak = 0;
    stats_since_us = now;
}

MicroWakeWord::MicroWakeWord() : impl_(std::make_unique<Impl>()) {}

MicroWakeWord::~MicroWakeWord() { impl_->Release(); }

bool MicroWakeWord::Initialize(AudioCodec* codec, srmodel_list_t* /*models_list*/) {
    Impl& s = *impl_;
    if (s.ready.load()) {
        return true;
    }
    s.codec = codec;
    if (g_jarvis_mww_model_size == 0) {
        ESP_LOGW(TAG, "No model linked in (mww_model_data.c is the placeholder), disabled");
        return false;
    }
    if (!s.LoadModel() || !s.SetupFrontend() || !s.StartTask()) {
        s.Release();
        return false;
    }
    s.ready = true;
    ESP_LOGI(TAG,
             "Ready for \"%s\": model %u B, stride %d, cutoff %u/255 (%d%%), window %d, "
             "refractory %d ms",
             s.wake_word.c_str(), g_jarvis_mww_model_size, s.stride, kCutoff,
             CONFIG_JARVIS_MWW_CUTOFF_PCT, kSlidingWindow, kRefractoryMs);
    return true;
}

void MicroWakeWord::Feed(const std::vector<int16_t>& data) {
    const int channels = impl_->codec != nullptr ? impl_->codec->input_channels() : 1;
    if (channels <= 1) {
        FeedMono(data.data(), data.size());
        return;
    }
    const size_t step = static_cast<size_t>(channels);
    std::vector<int16_t> mono;
    mono.reserve(data.size() / step);
    for (size_t i = 0; i < data.size(); i += step) {
        mono.push_back(data[i]);
    }
    FeedMono(mono.data(), mono.size());
}

void MicroWakeWord::OnWakeWordDetected(
    std::function<void(const std::string& wake_word)> callback) {
    impl_->callback = std::move(callback);
}

void MicroWakeWord::Start() {
    Impl& s = *impl_;
    if (!s.ready.load()) {
        return;
    }
    s.detected = false;
    s.reset_pending = true;  // handled by the inference task before new audio
    s.running = true;
}

void MicroWakeWord::Stop() {
    impl_->running = false;
    impl_->detected = false;
}

size_t MicroWakeWord::GetFeedSize() { return kStepSamples; }

const std::string& MicroWakeWord::GetLastDetectedWakeWord() const { return impl_->wake_word; }

bool MicroWakeWord::FeedMono(const int16_t* data, size_t samples) {
    Impl& s = *impl_;
    if (!s.running.load()) {
        return false;
    }
    if (s.detected.exchange(false)) {
        // Like CustomWakeWord: stay quiet until the next Start().
        s.running = false;
        if (s.callback) {
            s.callback(s.wake_word);
        }
        return true;
    }
    const size_t bytes = samples * sizeof(int16_t);
    const size_t sent = xStreamBufferSend(s.queue, data, bytes, 0);
    if (sent < bytes) {
        s.dropped_bytes.fetch_add(static_cast<uint32_t>(bytes - sent));
    }
    return false;
}

#endif  // CONFIG_JARVIS_MICRO_WAKE_WORD
