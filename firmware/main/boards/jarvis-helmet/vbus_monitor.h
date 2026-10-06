// 5V_MAIN voltage monitor (HW1 VSENSE divider on GPIO1 = ADC1_CH0).
#pragma once

#include <esp_adc/adc_oneshot.h>

class VbusMonitor {
public:
    // divider_ratio: 5V_MAIN / V_gpio (2.0 for the 100k/100k divider).
    VbusMonitor(adc_unit_t unit, adc_channel_t channel, float divider_ratio);
    ~VbusMonitor();

    // Samples periodically (esp_timer) with a moving average. Safe to call once.
    void Start(int period_ms = 1000);

    // Last averaged 5V_MAIN in millivolts; 0 if the ADC is unavailable.
    int GetMillivolts() const;
    // True when the averaged value is below VBUS_LOW_WARN_MV (config.h).
    bool IsLow() const;

    VbusMonitor(const VbusMonitor&) = delete;
    VbusMonitor& operator=(const VbusMonitor&) = delete;

private:
    struct Impl;
    Impl* impl_ = nullptr;
};
