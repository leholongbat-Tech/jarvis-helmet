"""Tests for latency.py on a synthetic xiaozhi-server log (contract §4.10 [LAT] format)."""

import csv
import json

import pytest

import latency

BASE = 1_791_000_000_000  # realistic epoch ms; deltas below are relative to it
FILE_PREFIX = "2026-10-06 10:00:00.123 - 0.9.7_SiOpJaJaVi - core.connection - INFO - core.connection - "


def _kv(kv):
    return "".join(f" {k}={BASE + v if k.startswith('speech_end') else v}"
                   for k, v in kv.items())


def lat(sid, stage, t, **kv):
    return f"{FILE_PREFIX}[LAT] sid={sid} stage={stage} t_ms={BASE + t}{_kv(kv)}"


def ansi(sid, stage, t, **kv):
    return (f"\x1b[32m261006 10:00:01\x1b[0m[0.9.7_SiOpJaJaVi][\x1b[94mcore.connection\x1b[0m]-"
            f"\x1b[1mINFO\x1b[0m-\x1b[92m[LAT] sid={sid} stage={stage} t_ms={BASE + t}{_kv(kv)}"
            "\x1b[0m")


SESSION_A = [
    lat("aaa", "mcp_call", 999_000, tool="self_sequence_save", ms=30),      # sync at connect
    lat("aaa", "vad_end", 1_000_000, speech_end=999_300),                    # turn 1: LLM
    lat("aaa", "asr_done", 1_000_150, text_len=12),
    ansi("aaa", "llm_first_token", 1_000_450),
    lat("aaa", "tts_first_audio", 1_000_700),
    lat("aaa", "first_packet_sent", 1_000_705),
    lat("aaa", "llm_done", 1_001_200),
    lat("aaa", "vad_end", 1_010_000, speech_end=1_009_280),                  # turn 2: quick
    lat("aaa", "asr_done", 1_010_120, text_len=9),
    lat("aaa", "quick_phrase", 1_010_121, task="open_mask"),
    lat("aaa", "mcp_call", 1_010_125, tool="self_sequence_run", ms=15),     # t = request
    lat("aaa", "tts_first_audio", 1_010_300),
    lat("aaa", "first_packet_sent", 1_010_302),
    lat("aaa", "vad_end", 1_020_000),                                        # turn 3: tool call
    lat("aaa", "asr_done", 1_020_200, text_len=30),
    lat("aaa", "llm_first_token", 1_020_500),
    lat("aaa", "mcp_call", 1_020_540, tool="self_sequence_run", ms=20),
    lat("aaa", "llm_first_token", 1_020_900),                                # 2nd LLM round
    lat("aaa", "tts_first_audio", 1_021_100),
    lat("aaa", "first_packet_sent", 1_021_104),
    lat("aaa", "llm_done", 1_021_300),
]
SESSION_B = [  # text injected with listen/detect: no vad_end, no asr_done
    lat("bbb", "llm_first_token", 2_000_300),
    lat("bbb", "tts_first_audio", 2_000_500),
    lat("bbb", "first_packet_sent", 2_000_510),
    lat("bbb", "llm_done", 2_000_800),
    lat("bbb", "quick_phrase", 2_005_000, task="close_mask"),
    lat("bbb", "mcp_call", 2_005_020, tool="self_sequence_run", ms=10),
    lat("bbb", "tts_first_audio", 2_005_200),
    lat("bbb", "first_packet_sent", 2_005_201),
    lat("bbb", "llm_first_token", 2_010_000),
    lat("bbb", "first_packet_sent", 2_010_400),
]
SESSION_C = [  # duplicate vad_end stays one turn; SV-C's key name speech_end_ms
    lat("ccc", "vad_end", 3_000_000, speech_end_ms=2_999_300, src="vad", mode="auto"),
    lat("ccc", "vad_end", 3_000_010),
    lat("ccc", "asr_done", 3_000_100, text_len=5),
    lat("ccc", "llm_first_token", 3_000_400),
    lat("ccc", "tts_first_audio", 3_000_600),
    lat("ccc", "first_packet_sent", 3_000_610),
]
NOISE = [
    "2026-10-06 10:00:00.100 - 0.9.7_x - core.connection - INFO - core.connection - 收到hello消息：{}",
    "[LAT] sid=bbb stage=asr_done",                        # malformed: no t_ms
    lat("aaa", "tts_req", 1_000_600),                      # unknown stage, ignored
    "",
]


def interleave(*sessions):
    out, idx = [], [0] * len(sessions)
    while any(i < len(s) for i, s in zip(idx, sessions)):
        for n, s in enumerate(sessions):
            if idx[n] < len(s):
                out.append(s[idx[n]])
                idx[n] += 1
    return out


@pytest.fixture()
def log_file(tmp_path):
    path = tmp_path / "server.log"
    lines = NOISE[:1] + interleave(SESSION_A, SESSION_B, SESSION_C) + NOISE[1:]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_parse_line_variants():
    ev, err = latency.parse_line(lat("s1", "mcp_call", 123, tool="self_mask_open", ms=12))
    assert err is None and (ev.sid, ev.stage, ev.t_ms) == ("s1", "mcp_call", BASE + 123)
    assert ev.fields == {"tool": "self_mask_open", "ms": "12"} and ev.num("ms") == 12.0
    ev, _ = latency.parse_line(ansi("s2", "vad_end", 456, speech_end=400))
    assert (ev.sid, ev.t_ms, ev.num("speech_end")) == ("s2", BASE + 456, BASE + 400.0)
    assert latency.parse_line("no marker here") == (None, None)
    ev, err = latency.parse_line("[LAT] sid=x stage=asr_done", "f", 7)
    assert ev is None and err == "f:7: missing sid/stage/t_ms"
    ev, err = latency.parse_line("[LAT] sid=x stage=asr_done t_ms=abc")
    assert ev is None and "bad t_ms" in err
    ev, _ = latency.parse_line("[LAT] sid=x stage=asr_done t_ms=1791222111566.0 text_len=3\r\n")
    assert ev.t_ms == 1791222111566 and ev.fields["text_len"] == "3"


def test_grouping_and_turn_metrics(log_file):
    events, stats = latency.parse_files([str(log_file)])
    assert stats.lat_lines == len(SESSION_A) + len(SESSION_B) + len(SESSION_C) + 1
    assert stats.malformed and stats.unknown_stages == {"tts_req": 1}
    turns = latency.group_turns(events)
    user = [t for t in turns if t.is_user_turn]
    assert [(t.sid, t.index, t.kind) for t in user] == [
        ("aaa", 1, "voice"), ("aaa", 2, "quick"), ("aaa", 3, "voice"),
        ("bbb", 1, "text"), ("bbb", 2, "quick"), ("bbb", 3, "text"), ("ccc", 1, "voice")]
    background = [t for t in turns if not t.is_user_turn]
    assert len(background) == 1 and background[0].events[0].fields["tool"] == "self_sequence_save"

    m1 = latency.turn_metrics(user[0])
    assert (m1["vad_wait"], m1["asr"], m1["llm_ttft"], m1["llm_total"]) == (700, 150, 300, 1050)
    assert (m1["tts_first"], m1["send_first"], m1["server_total"], m1["eos_to_audio"]) == \
        (250, 5, 705, 1405)
    assert m1["eos_to_mcp"] is None and m1["mcp_rtt"] == []

    m2 = latency.turn_metrics(user[1])
    assert (m2["asr_to_quick"], m2["eos_to_mcp"], m2["eos_to_mcp_quick"]) == (1, 845, 845)
    assert m2["eos_to_audio"] == 1022 and m2["mcp_rtt"] == [15.0]
    assert latency.turn_metrics(user[1], mcp_t_is_end=True)["eos_to_mcp"] == 830

    m3 = latency.turn_metrics(user[2])          # second LLM round stays in the same turn
    assert len(user[2].all("llm_first_token")) == 2
    assert (m3["llm_ttft"], m3["eos_to_mcp"], m3["eos_to_audio"]) == (300, 540, 1104)
    assert latency.turn_metrics(user[2], vad_offset_ms=700)["eos_to_audio"] == 1804
    assert m3["vad_wait"] is None              # no speech_end key

    m4 = latency.turn_metrics(user[3])          # text turn: no end-of-speech reference
    assert m4["eos_to_audio"] is None and m4["tts_first"] == 200

    m7 = latency.turn_metrics(user[6])
    assert m7["eos_to_audio"] == 1310 and m7["asr"] == 100  # first vad_end of the duplicate
    assert m7["vad_wait"] == 700


def test_passive_calls_and_ptt_ignore_offsets(tmp_path):
    """tests-tools-1/6: a late connect-time save is not the command; PTT eos = listen stop."""
    lines = [
        lat("ppp", "vad_end", 5_000_000, src="listen_stop", mode="manual"),   # PTT, no speech_end
        lat("ppp", "mcp_call", 5_000_125, tool="self_sequence_save", ms=20),  # sync, not the command
        lat("ppp", "asr_done", 5_000_300, text_len=9),
        lat("ppp", "mcp_call", 5_002_600, tool="self_sequence_run", ms=15),
        lat("ppp", "first_packet_sent", 5_002_700),
    ]
    path = tmp_path / "ptt.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    events, _ = latency.parse_files([str(path)])
    user = [t for t in latency.group_turns(events) if t.is_user_turn]
    assert len(user) == 1
    m = latency.turn_metrics(user[0], vad_offset_ms=700)
    assert m["eos_to_mcp"] == 2600
    assert m["eos_to_audio"] == 2700


def test_summary_statistics(log_file):
    events, _ = latency.parse_files([str(log_file)])
    turns = latency.group_turns(events)
    rows = {r["metric"]: r for r in latency.summarize(turns, 0.0, {})}
    audio = rows["eos_to_audio"]
    assert audio["n"] == 4 and audio["median"] == 1207.0 and audio["p90"] == 1376.5
    assert (audio["min"], audio["max"], audio["target_ms"], audio["pass"]) == (1022, 1405, 1500,
                                                                              True)
    rtt = rows["mcp_rtt"]
    assert rtt["n"] == 4 and rtt["median"] == 17.5 and rtt["p90"] == 27.0 and rtt["pass"] is True
    quick = rows["eos_to_mcp_quick"]
    assert quick["n"] == 1 and quick["median"] == 845 and quick["pass"] is False
    assert rows["vad_wait"]["target_ms"] is None and rows["vad_wait"]["pass"] is None
    four_g = {r["metric"]: r for r in latency.summarize(turns, 0.0, {"eos_to_audio": 2500.0})}
    assert four_g["eos_to_audio"]["target_ms"] == 2500.0


def test_percentile_linear_interpolation():
    assert latency.percentile([], 90) is None
    assert latency.percentile([5], 90) == 5
    assert latency.percentile([10, 15, 20, 30], 50) == 17.5
    assert latency.percentile(list(range(1, 11)), 90) == pytest.approx(9.1)


def test_cli_table_csv_and_check(log_file, tmp_path, capsys):
    out_csv = tmp_path / "turns.csv"
    assert latency.main([str(log_file), "--csv", str(out_csv), "--turns"]) == 0
    text = capsys.readouterr().out
    assert "eos_to_audio" in text and "KHÔNG ĐẠT" in text and "ĐẠT" in text
    assert "7 lượt nói" in text and "stage lạ" in text
    with open(out_csv, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 8  # 7 user turns + 1 background
    assert {"sid", "turn", "kind", "task", "tools", "t_vad_end", "eos_to_audio",
            "eos_to_mcp_quick", "mcp_rtt"} <= set(rows[0])
    quick = next(r for r in rows if r["task"] == "open_mask")
    assert quick["kind"] == "quick" and float(quick["eos_to_mcp"]) == 845
    assert latency.main([str(log_file), "--check"]) == 1  # quick-phrase target missed
    capsys.readouterr()


def test_cli_filters_and_json(log_file, capsys):
    assert latency.main([str(log_file), "--json", "--sid", "bb"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["info"]["sessions"] == 1 and data["info"]["user_turns"] == 3
    assert latency.main([str(log_file), "--json", "--kind", "quick"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["info"]["user_turns"] == 2 and all(t["kind"] == "quick" for t in data["turns"])
    assert latency.main([str(log_file), "--json", "--last", "2", "--network", "4g"]) == 0
    data = json.loads(capsys.readouterr().out)
    target = next(r for r in data["metrics"] if r["metric"] == "eos_to_audio")["target_ms"]
    assert data["info"]["user_turns"] == 2 and target == 2500.0
    assert latency.main([str(log_file), "--json", "--since", str(BASE + 2_000_000),
                         "--until", str((BASE + 2_999_999) / 1000)]) == 0  # ms and s
    data = json.loads(capsys.readouterr().out)
    assert {t["sid"] for t in data["turns"]} == {"bbb"}


def test_cli_no_lat_lines(tmp_path, capsys):
    empty = tmp_path / "empty.log"
    empty.write_text("nothing to see\n", encoding="utf-8")
    assert latency.main([str(empty)]) == 3
    assert "no [LAT] lines" in capsys.readouterr().err


def test_glob_and_multiple_files(tmp_path, capsys):
    (tmp_path / "server.log").write_text("\n".join(SESSION_A) + "\n", encoding="utf-8")
    (tmp_path / "server.2026-10-05.log").write_text("\n".join(SESSION_C) + "\n",
                                                    encoding="utf-8")
    assert latency.main([str(tmp_path / "server*.log"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["info"]["sessions"] == 2 and data["info"]["user_turns"] == 4
