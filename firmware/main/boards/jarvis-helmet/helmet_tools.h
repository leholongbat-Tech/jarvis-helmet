// Board-level MCP tools that are not owned by a single controller (FW4):
// self.sequence.run / run_saved / save / stop, self.display.show_info, self.system.get_status.
#pragma once

#include <functional>
#include <string>

class MaskController;
class OutputController;
class SequenceRunner;
class VbusMonitor;

namespace helmet_tools {

using ShowInfoFn = std::function<void(const std::string& title, const std::string& text,
                                      int seconds)>;

// Smoothed RMS of the microphone and of the decoded playback (before the volume scaling),
// in dBFS; -100 = digital silence or no data (MeteredAudioCodec::kSilenceDbfs). mic_probe:
// where the boot probe found the microphone (MeteredAudioCodec::MicProbeName).
struct AudioLevels {
    float mic_dbfs = -100.0f;
    float speaker_dbfs = -100.0f;
    const char* mic_probe = "not_run";
};
using AudioLevelsFn = std::function<AudioLevels()>;

void Register(SequenceRunner& runner, MaskController& mask, OutputController& output,
              VbusMonitor& vbus, ShowInfoFn show_info, AudioLevelsFn audio_levels);

// {"mask":{"state":..,"position":..},"eyes":{"on":..,"brightness":..,"effect":..},
//  "output":{"on":..},"sequence":{"running":..,"name":..},"vbus_mv":..,"rssi":..,
//  "uptime_s":..,"free_heap":..,"audio":{"mic_dbfs":..,"speaker_dbfs":..,"mic_probe":..}}
std::string BuildStatusJson(MaskController& mask, OutputController& output,
                            SequenceRunner& runner, VbusMonitor& vbus, const AudioLevels& audio);

}  // namespace helmet_tools
