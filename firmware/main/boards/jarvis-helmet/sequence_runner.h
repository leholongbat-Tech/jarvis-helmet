// Sequence runner (FW4): runs validated step lists on its own task.
//
// Run()/RunSaved()/Save()/Stop() are called from MCP callbacks (main task) and from button
// callbacks (esp_timer task); they validate, queue and return without waiting.
#pragma once

#include <expected>
#include <functional>
#include <string>

class MaskController;
class OutputController;

class SequenceRunner {
public:
    using ShowInfoFn = std::function<void(const std::string& title, const std::string& text,
                                          int seconds)>;
    // Called on the runner task: must queue the sound and return without waiting for it
    // (cancel is checked every 20 ms). Returns false for an unknown sound name.
    using PlaySoundFn = std::function<bool(const std::string& name)>;

    SequenceRunner(MaskController& mask, OutputController& output, ShowInfoFn show_info,
                   PlaySoundFn play_sound = nullptr);
    ~SequenceRunner();

    // Creates the "seq_runner" task (6 KB internal-RAM stack: it may write NVS).
    void Start();

    // Validates and queues a sequence. A new sequence cancels the running one within one
    // 20 ms tick: servos stop where they are, lights keep their state. Errors are
    // JSON-safe strings such as "invalid step #3: speed out of range 10..100".
    std::expected<void, std::string> Run(const std::string& steps_json,
                                         const std::string& name = "");
    // Looks up NVS "seq_<name>" first, then the built-in sequences.
    std::expected<void, std::string> RunSaved(const std::string& name);
    // Validates, then stores the JSON as NVS blob "seq_<name>" (name [a-z0-9_]{1,11}).
    std::expected<void, std::string> Save(const std::string& name, const std::string& steps_json);
    void Stop();

    bool IsRunning() const;
    std::string CurrentName() const;  // name of the running sequence, empty if anonymous/idle

    // Built-in fallbacks used when NVS has no saved copy: "open_mask", "close_mask"
    // (same steps as server/tasks/tasks.yaml). Returns nullptr for unknown names.
    static const char* BuiltinSequence(const std::string& name);

    SequenceRunner(const SequenceRunner&) = delete;
    SequenceRunner& operator=(const SequenceRunner&) = delete;

private:
    struct Impl;
    Impl* impl_ = nullptr;
};
