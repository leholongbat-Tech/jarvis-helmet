"""Tests for heap_trend.py on synthetic ESP-IDF serial logs."""

import json

import heap_trend

BANNER = "ESP-ROM:esp32s3-20210327\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)"


def sram(ms, free, minimal=90000):
    return f"I ({ms}) SystemInfo: free sram: {free} minimal sram: {minimal}"


def write(tmp_path, lines):
    path = tmp_path / "helmet.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_stable_heap_passes(tmp_path, capsys):
    lines = [BANNER] + [sram(10000 * (i + 1), 120000 + (300 if i % 2 else -300))
                        for i in range(30)]
    assert heap_trend.main([write(tmp_path, lines), "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["samples"] == 30 and res["boots"] == 1 and res["reset_reasons"] == ["POWERON"]
    assert abs(res["drop_bytes"]) < 1000 and res["pass"] is True


def test_gap_in_samples_fails_instead_of_hiding_a_leak(tmp_path, capsys):
    # Idle samples, then 8 minutes without any heap line (busy device, old firmware), then one
    # sample 12 KB lower: the 10 %-of-samples comparison used to average the leak away.
    lines = [sram(10000 * (i + 1), 120000) for i in range(30)]
    lines.append(sram(300000 + 480000, 108000))
    assert heap_trend.main([write(tmp_path, lines), "--json"]) == 1
    res = json.loads(capsys.readouterr().out)
    gap = next(c for c in res["checks"] if c["name"] == "no_sample_gaps")
    assert gap["ok"] is False and res["largest_gap_s"] == 480.0  # 300 s -> 780 s


def test_time_windows_compare_start_and_end_of_the_run(tmp_path, capsys):
    # Samples every 10 s from 10 s to 600 s; the last window (540-600 s, 7 samples) is 6 KB lower.
    lines = [sram(10000 * (i + 1), 120000 if i < 53 else 114000) for i in range(60)]
    heap_trend.main([write(tmp_path, lines), "--json"])
    res = json.loads(capsys.readouterr().out)
    assert res["drop_bytes"] == 6000


def test_steady_leak_fails(tmp_path, capsys):
    lines = [sram(10000 * (i + 1), 120000 - 1000 * i) for i in range(30)]
    assert heap_trend.main([write(tmp_path, lines), "--json"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["slope_bytes_per_hour"] < -300000
    assert next(c for c in res["checks"] if c["name"] == "no_steady_decrease")["ok"] is False


def test_reboot_detected(tmp_path, capsys):
    first = [sram(10000 * (i + 1), 120000) for i in range(10)]
    second = [sram(10000 * (i + 1), 121000) for i in range(10)]   # timestamps restart
    path = write(tmp_path, first + ["Guru Meditation Error"] + second)
    assert heap_trend.main([path, "--json"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["boots"] == 2 and res["samples"] == 10
    assert heap_trend.main([path, "--allow-reboot"]) == 0
    assert "ĐẠT" in capsys.readouterr().out
    banner_reset = write(tmp_path, first + [BANNER.replace("POWERON", "SW_CPU_RESET")] + second)
    assert heap_trend.main([banner_reset, "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["reset_reasons"] == ["SW_CPU_RESET"]


def test_low_minimal_sram_and_json_status_lines(tmp_path, capsys):
    lines = [sram(10000 * (i + 1), 60000, minimal=20000) for i in range(8)]
    assert heap_trend.main([write(tmp_path, lines), "--json"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["minimal_sram"] == 20000
    status = [f'I ({10000 * (i + 1)}) HelmetTools: status {{"vbus_mv":5040,"free_heap":{150000}}}'
              for i in range(8)]
    assert heap_trend.main([write(tmp_path, status), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["minimal_sram"] is None


def test_no_samples(tmp_path, capsys):
    assert heap_trend.main([write(tmp_path, ["hello"])]) == 3
    assert heap_trend.main([write(tmp_path, [sram(1000, 1), sram(2000, 1)]),
                            "--json"]) == 1  # too few samples
    capsys.readouterr()
