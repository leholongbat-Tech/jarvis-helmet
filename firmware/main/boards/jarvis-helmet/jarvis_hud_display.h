// Iron Man style HUD on the 2.4" ST7789 in landscape, 320x240 (FW6).
//
// Built on top of the stock SpiLcdDisplay UI: SetupUI() lets the base class create its
// widgets (other upstream code such as Assets::Apply and SetTheme touch them), hides
// them, then draws the HUD on an opaque root above. Every public method is thread-safe
// (takes DisplayLockGuard internally) and may be called from any task.
#pragma once

#include "display/lcd_display.h"
#include "mask_controller.h"

#include <functional>
#include <memory>
#include <string>

// Live data the HUD polls on its own timer; every member is optional.
struct HudDataSource {
    std::function<MaskState()> mask_state;
    std::function<void(bool& on, int& brightness)> eyes_state;
    std::function<int()> vbus_mv;
    std::function<float()> mic_level;      // 0..1
    std::function<float()> speaker_level;  // 0..1
};

class JarvisHudDisplay : public SpiLcdDisplay {
public:
    JarvisHudDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel, int width,
                     int height, int offset_x, int offset_y, bool mirror_x, bool mirror_y,
                     bool swap_xy);
    ~JarvisHudDisplay() override;

    // Display overrides.
    void SetupUI() override;
    void SetStatus(const char* status) override;
    void ShowNotification(const char* notification, int duration_ms = 3000) override;
    void ShowNotification(const std::string& notification, int duration_ms = 3000) override;
    void SetEmotion(const char* emotion) override;
    void SetChatMessage(const char* role, const char* content) override;
    void ClearChatMessages() override;
    void SetTheme(Theme* theme) override;
    void UpdateStatusBar(bool update_all = false) override;
    bool SetTextFont(std::shared_ptr<LvglFont> text_font) override;
    void SetHideSubtitle(bool hide) override;
    void SetPreviewImage(std::unique_ptr<LvglImage> image) override;

    // HUD-specific API.
    void SetDataSource(HudDataSource source);
    // Info card sliding over the chat area, hidden after `seconds` (self.display.show_info).
    void ShowInfo(const std::string& title, const std::string& text, int seconds);
    void SetMaskState(const MaskState& state);
    void SetEyesState(bool on, int brightness);
    void SetVbus(int millivolts);
    // OFFLINE badge + red reactor (Wi-Fi down or server unreachable).
    void SetOffline(bool offline);

private:
    struct Impl;
    Impl* impl_ = nullptr;
};
