// 5V_MAIN voltage monitor (HW1 VSENSE divider on GPIO1 = ADC1_CH0, FW4 get_status/HUD).
//
// An esp_timer samples the ADC (a short burst of back-to-back oneshot reads, no delays:
// the esp_timer task is shared with the buttons), converts the result with the eFuse
// curve-fitting calibration when available, scales it by the divider ratio and keeps a
// moving average. Readers only load an atomic.
#include "vbus_monitor.h"

#include <esp_adc/adc_cali.h>
#include <esp_adc/adc_cali_scheme.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <soc/soc_caps.h>

#include <atomic>
#include <cmath>
#include <cstdint>

#if __has_include("config.h")
#include "config.h"
#endif

#ifndef VBUS_LOW_WARN_MV
#define VBUS_LOW_WARN_MV 4600
#endif

namespace {

constexpr char TAG[] = "VbusMonitor";

constexpr int kWindow = 8;      // moving average length, in timer samples
constexpr int kBurstReads = 4;  // raw reads averaged into one sample
constexpr int kMinPeriodMs = 50;

// Without calibration: ADC_ATTEN_DB_12 spans roughly 0..3100 mV over the raw range.
constexpr int kUncalibratedFullScaleMv = 3100;
#ifdef SOC_ADC_RTC_MAX_BITWIDTH
constexpr int kRawMax = (1 << SOC_ADC_RTC_MAX_BITWIDTH) - 1;
#else
constexpr int kRawMax = 4095;
#endif

// The 3.3 V regulator stops working long before 5V_MAIN falls this low, so a lower
// reading means the divider is not fitted (e.g. a bare dev board on USB): not "low".
constexpr int kMinPlausibleMv = 2500;

}  // namespace

struct VbusMonitor::Impl {
    adc_channel_t channel = ADC_CHANNEL_0;
    float divider_ratio = 2.0f;
    adc_oneshot_unit_handle_t adc = nullptr;
    adc_cali_handle_t cali = nullptr;
    esp_timer_handle_t timer = nullptr;
    std::atomic<int> millivolts{0};
    std::atomic<bool> started{false};

    // Touched by Start() before the timer exists, then only by the timer callback.
    int window[kWindow] = {};
    int filled = 0;
    int next = 0;
    int64_t sum = 0;
    bool read_error_logged = false;

    bool Sample(int& mv) {
        int64_t raw_sum = 0;
        int reads = 0;
        for (int i = 0; i < kBurstReads; ++i) {
            int raw = 0;
            if (adc_oneshot_read(adc, channel, &raw) == ESP_OK) {
                raw_sum += raw;
                ++reads;
            }
        }
        if (reads == 0) {
            if (!read_error_logged) {
                read_error_logged = true;
                ESP_LOGW(TAG, "ADC read failed");
            }
            return false;
        }
        const int raw = static_cast<int>((raw_sum + reads / 2) / reads);
        int pin_mv = 0;
        if (cali == nullptr || adc_cali_raw_to_voltage(cali, raw, &pin_mv) != ESP_OK) {
            pin_mv = raw * kUncalibratedFullScaleMv / kRawMax;
        }
        mv = static_cast<int>(std::lround(pin_mv * divider_ratio));
        return true;
    }

    void Push(int mv) {
        if (filled < kWindow) {
            ++filled;
        } else {
            sum -= window[next];
        }
        window[next] = mv;
        sum += mv;
        next = (next + 1) % kWindow;
        millivolts.store(static_cast<int>(sum / filled), std::memory_order_relaxed);
    }

    static void OnTimer(void* arg) {
        auto* self = static_cast<Impl*>(arg);
        int mv = 0;
        if (self->Sample(mv)) {
            self->Push(mv);
        }
    }
};

VbusMonitor::VbusMonitor(adc_unit_t unit, adc_channel_t channel, float divider_ratio)
    : impl_(new Impl()) {
    impl_->channel = channel;
    impl_->divider_ratio = divider_ratio;

    adc_oneshot_unit_init_cfg_t unit_config = {};
    unit_config.unit_id = unit;
    unit_config.ulp_mode = ADC_ULP_MODE_DISABLE;
    esp_err_t err = adc_oneshot_new_unit(&unit_config, &impl_->adc);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ADC unit %d unavailable: %s", static_cast<int>(unit), esp_err_to_name(err));
        impl_->adc = nullptr;
        return;
    }

    adc_oneshot_chan_cfg_t channel_config = {};
    channel_config.atten = ADC_ATTEN_DB_12;
    channel_config.bitwidth = ADC_BITWIDTH_DEFAULT;
    err = adc_oneshot_config_channel(impl_->adc, channel, &channel_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ADC channel %d config failed: %s", static_cast<int>(channel),
                 esp_err_to_name(err));
        adc_oneshot_del_unit(impl_->adc);
        impl_->adc = nullptr;
        return;
    }

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    adc_cali_curve_fitting_config_t cali_config = {};
    cali_config.unit_id = unit;
    cali_config.chan = channel;
    cali_config.atten = ADC_ATTEN_DB_12;
    cali_config.bitwidth = ADC_BITWIDTH_DEFAULT;
    err = adc_cali_create_scheme_curve_fitting(&cali_config, &impl_->cali);
#elif ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    adc_cali_line_fitting_config_t cali_config = {};
    cali_config.unit_id = unit;
    cali_config.atten = ADC_ATTEN_DB_12;
    cali_config.bitwidth = ADC_BITWIDTH_DEFAULT;
    err = adc_cali_create_scheme_line_fitting(&cali_config, &impl_->cali);
#else
    err = ESP_ERR_NOT_SUPPORTED;
#endif
    if (err != ESP_OK) {
        impl_->cali = nullptr;
        ESP_LOGW(TAG, "ADC calibration unavailable (%s), using the raw scale",
                 esp_err_to_name(err));
    }
}

VbusMonitor::~VbusMonitor() {
    if (impl_->timer != nullptr) {
        esp_timer_stop(impl_->timer);
        esp_timer_delete(impl_->timer);
    }
    if (impl_->cali != nullptr) {
#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
        adc_cali_delete_scheme_curve_fitting(impl_->cali);
#elif ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
        adc_cali_delete_scheme_line_fitting(impl_->cali);
#endif
    }
    if (impl_->adc != nullptr) {
        adc_oneshot_del_unit(impl_->adc);
    }
    delete impl_;
}

void VbusMonitor::Start(int period_ms) {
    if (impl_->started.exchange(true)) {
        return;
    }
    if (impl_->adc == nullptr) {
        ESP_LOGW(TAG, "Not started: no ADC");
        return;
    }
    // First value right away so the HUD and get_status do not show 0 for a period.
    int mv = 0;
    if (impl_->Sample(mv)) {
        impl_->Push(mv);
    }

    esp_timer_create_args_t timer_args = {};
    timer_args.callback = &Impl::OnTimer;
    timer_args.arg = impl_;
    timer_args.dispatch_method = ESP_TIMER_TASK;
    timer_args.name = "vbus_monitor";
    timer_args.skip_unhandled_events = true;
    esp_err_t err = esp_timer_create(&timer_args, &impl_->timer);
    if (err == ESP_OK) {
        const int period = period_ms > kMinPeriodMs ? period_ms : kMinPeriodMs;
        err = esp_timer_start_periodic(impl_->timer, static_cast<uint64_t>(period) * 1000);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Sampling timer failed: %s", esp_err_to_name(err));
        return;
    }
    ESP_LOGI(TAG, "5V_MAIN %d mV (%s, divider x%.2f, low below %d mV)", GetMillivolts(),
             impl_->cali != nullptr ? "calibrated" : "uncalibrated",
             static_cast<double>(impl_->divider_ratio), VBUS_LOW_WARN_MV);
}

int VbusMonitor::GetMillivolts() const { return impl_->millivolts.load(std::memory_order_relaxed); }

bool VbusMonitor::IsLow() const {
    const int mv = GetMillivolts();
    return mv >= kMinPlausibleMv && mv < VBUS_LOW_WARN_MV;
}
