// Jarvis Helmet board configuration (pin map from docs/modules/HW2, contract §3.3).
//
// Based on bread-compact-wifi-lcd/config.h. Differences: LCD reset moved from the GPIO45
// strapping pin to GPIO39, no LampController (GPIO18 is servo S1), helmet actuators,
// buttons and the 5V_MAIN voltage sense added.
#ifndef _BOARD_CONFIG_H_
#define _BOARD_CONFIG_H_

#include <driver/gpio.h>
#include <driver/ledc.h>
#include <hal/adc_types.h>

// ---------------------------------------------------------------------------
// Audio: INMP441 microphone + MAX98357A amplifier, simplex I2S.
// ---------------------------------------------------------------------------
#define AUDIO_INPUT_SAMPLE_RATE  16000
// The MAX98357A accepts LRCLK of 8, 16, 32, 44.1, 48, 88.2 or 96 kHz only; 11.025, 12,
// 22.05 and 24 kHz are "NOT supported" (datasheet, Digital Audio Interface) and come out
// distorted. bread-compact-wifi's 24000 did exactly that, so the speaker runs at 48 kHz and
// AudioService resamples the 24 kHz TTS stream (and the 16/24 kHz OGG sounds) up to it.
#define AUDIO_OUTPUT_SAMPLE_RATE 48000

#define AUDIO_I2S_METHOD_SIMPLEX

#define AUDIO_I2S_MIC_GPIO_WS   GPIO_NUM_4
#define AUDIO_I2S_MIC_GPIO_SCK  GPIO_NUM_5
#define AUDIO_I2S_MIC_GPIO_DIN  GPIO_NUM_6
#define AUDIO_I2S_SPK_GPIO_DOUT GPIO_NUM_7
#define AUDIO_I2S_SPK_GPIO_BCLK GPIO_NUM_15
#define AUDIO_I2S_SPK_GPIO_LRCK GPIO_NUM_16

// ---------------------------------------------------------------------------
// Status LED and buttons (active low, internal pull-up).
// ---------------------------------------------------------------------------
// WS2812 on ESP32-S3-DevKitC-1 v1.0 and most N16R8 clones. DevKitC-1 v1.1 boards have
// the RGB LED on GPIO38 instead: change this define (GPIO38 is free on this pin map).
#define BUILTIN_LED_GPIO        GPIO_NUM_48
#define BOOT_BUTTON_GPIO        GPIO_NUM_0
#define PTT_BUTTON_GPIO         GPIO_NUM_10
#define MASK_BUTTON_GPIO        GPIO_NUM_11

// ---------------------------------------------------------------------------
// LCD: ST7789 1.54" 240x240 on SPI3.
// ---------------------------------------------------------------------------
#define DISPLAY_BACKLIGHT_PIN GPIO_NUM_42
#define DISPLAY_MOSI_PIN      GPIO_NUM_47
#define DISPLAY_CLK_PIN       GPIO_NUM_21
#define DISPLAY_DC_PIN        GPIO_NUM_40
#define DISPLAY_RST_PIN       GPIO_NUM_39

#if defined(CONFIG_LCD_ST7789_240X240)
#define LCD_TYPE_ST7789_SERIAL
#define DISPLAY_CS_PIN        GPIO_NUM_41
#define DISPLAY_WIDTH   240
#define DISPLAY_HEIGHT  240
#define DISPLAY_MIRROR_X false
#define DISPLAY_MIRROR_Y false
#define DISPLAY_SWAP_XY false
#define DISPLAY_INVERT_COLOR    true
#define DISPLAY_RGB_ORDER  LCD_RGB_ELEMENT_ORDER_RGB
#define DISPLAY_OFFSET_X  0
#define DISPLAY_OFFSET_Y  0
#define DISPLAY_BACKLIGHT_OUTPUT_INVERT false
#define DISPLAY_SPI_MODE 0
#elif defined(CONFIG_LCD_ST7789_240X240_7PIN)
// 7-pin modules have no CS pad: CS is tied low on the module, SPI mode 3.
#define LCD_TYPE_ST7789_SERIAL
#define DISPLAY_CS_PIN        GPIO_NUM_NC
#define DISPLAY_WIDTH   240
#define DISPLAY_HEIGHT  240
#define DISPLAY_MIRROR_X false
#define DISPLAY_MIRROR_Y false
#define DISPLAY_SWAP_XY false
#define DISPLAY_INVERT_COLOR    true
#define DISPLAY_RGB_ORDER  LCD_RGB_ELEMENT_ORDER_RGB
#define DISPLAY_OFFSET_X  0
#define DISPLAY_OFFSET_Y  0
#define DISPLAY_BACKLIGHT_OUTPUT_INVERT false
#define DISPLAY_SPI_MODE 3
#else
#error "jarvis-helmet: select LCD_ST7789_240X240 or LCD_ST7789_240X240_7PIN (LCD Type menu)"
#endif

// ---------------------------------------------------------------------------
// LEDC allocation. ESP32-S3 only has LEDC_LOW_SPEED_MODE.
//   timer 0 / channel 0: LCD backlight (upstream PwmBacklight, 25 kHz, 10 bit)
//   timer 1 / channels 1-2: mask servos (50 Hz, 14 bit)
//   timer 2 / channels 3-4: eye lights and spare output MOSFETs (5 kHz, 10 bit)
// Never use GpioLed on this board: it defaults to timer 1 / channel 0.
// ---------------------------------------------------------------------------
#define HELMET_LEDC_SPEED_MODE  LEDC_LOW_SPEED_MODE

// Mask servos (FW2). S0 = left, S1 = right (mounted mirrored).
#define SERVO_S0_GPIO           GPIO_NUM_17
#define SERVO_S1_GPIO           GPIO_NUM_18
#define SERVO_LEDC_TIMER        LEDC_TIMER_1
#define SERVO_S0_LEDC_CHANNEL   LEDC_CHANNEL_1
#define SERVO_S1_LEDC_CHANNEL   LEDC_CHANNEL_2
#define SERVO_LEDC_FREQ_HZ      50
#define SERVO_LEDC_RESOLUTION   LEDC_TIMER_14_BIT
#define SERVO_PULSE_MIN_US      500    // pulse at 0 degrees
#define SERVO_PULSE_MAX_US      2400   // pulse at 180 degrees

// Default calibration before self.servo.save_calibration (90 degrees +/- 10).
#define SERVO_S0_CLOSED_DEFAULT 80
#define SERVO_S0_OPEN_DEFAULT   100
#define SERVO_S1_CLOSED_DEFAULT 100
#define SERVO_S1_OPEN_DEFAULT   80

// S1 starts this much later than S0 to spread the inrush current; both finish together.
#define MASK_S1_START_DELAY_MS  30
// Stop the holding pulses after this idle time; 0 = always hold (faceplate falls otherwise).
#define MASK_DETACH_AFTER_MS    0

// Eye lights and spare output (FW3), logic-level MOSFET gates with pull-downs.
#define EYES_GPIO               GPIO_NUM_8
#define AUX_OUTPUT_GPIO         GPIO_NUM_9
#define OUTPUT_LEDC_TIMER       LEDC_TIMER_2
#define EYES_LEDC_CHANNEL       LEDC_CHANNEL_3
#define AUX_LEDC_CHANNEL        LEDC_CHANNEL_4
#define OUTPUT_LEDC_FREQ_HZ     5000
#define OUTPUT_LEDC_RESOLUTION  LEDC_TIMER_10_BIT
#define EYES_GAMMA              2.2f

// ---------------------------------------------------------------------------
// 5V_MAIN sense (HW1): 100k/100k divider on GPIO1 = ADC1 channel 0.
// ---------------------------------------------------------------------------
#define VSENSE_ADC_UNIT         ADC_UNIT_1
#define VSENSE_ADC_CHANNEL      ADC_CHANNEL_0
#define VSENSE_DIVIDER_RATIO    2.0f
#define VBUS_LOW_WARN_MV        4600

#endif // _BOARD_CONFIG_H_
