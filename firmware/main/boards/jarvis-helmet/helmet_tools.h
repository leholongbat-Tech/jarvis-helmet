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

void Register(SequenceRunner& runner, MaskController& mask, OutputController& output,
              VbusMonitor& vbus, ShowInfoFn show_info);

// {"mask":{"state":..,"position":..},"eyes":{"on":..,"brightness":..,"effect":..},
//  "output":{"on":..},"sequence":{"running":..,"name":..},"vbus_mv":..,"rssi":..,
//  "uptime_s":..,"free_heap":..}
std::string BuildStatusJson(MaskController& mask, OutputController& output,
                            SequenceRunner& runner, VbusMonitor& vbus);

}  // namespace helmet_tools
