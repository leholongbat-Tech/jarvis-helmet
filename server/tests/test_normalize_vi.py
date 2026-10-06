"""Vietnamese/English TTS text normalization and English-sentence detection (tts-router)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tts" / "router"))

from tts_router.normalize_vi import (  # noqa: E402
    Normalizer,
    NormalizerConfig,
    en_int,
    has_vietnamese_marks,
    is_english_sentence,
    looks_vietnamese_syllable,
    normalize_en,
    normalize_vi,
    parse_number,
    strip_markup,
    vi_int,
)
from tts_router.textsplit import split_sentences  # noqa: E402


# --- integers: mốt/một, tư/bốn, lăm/năm, linh, mười/mươi, groups --------------------------

@pytest.mark.parametrize("n,expected", [
    (0, "không"),
    (1, "một"),
    (4, "bốn"),
    (5, "năm"),
    (10, "mười"),
    (11, "mười một"),
    (14, "mười bốn"),
    (15, "mười lăm"),
    (19, "mười chín"),
    (20, "hai mươi"),
    (21, "hai mươi mốt"),
    (24, "hai mươi tư"),
    (25, "hai mươi lăm"),
    (31, "ba mươi mốt"),
    (40, "bốn mươi"),
    (44, "bốn mươi tư"),
    (45, "bốn mươi lăm"),
    (55, "năm mươi lăm"),
    (99, "chín mươi chín"),
    (100, "một trăm"),
    (101, "một trăm linh một"),
    (104, "một trăm linh bốn"),
    (105, "một trăm linh năm"),
    (110, "một trăm mười"),
    (111, "một trăm mười một"),
    (115, "một trăm mười lăm"),
    (121, "một trăm hai mươi mốt"),
    (124, "một trăm hai mươi tư"),
    (125, "một trăm hai mươi lăm"),
    (500, "năm trăm"),
    (1000, "một nghìn"),
    (1001, "một nghìn không trăm linh một"),
    (1005, "một nghìn không trăm linh năm"),
    (1010, "một nghìn không trăm mười"),
    (1015, "một nghìn không trăm mười lăm"),
    (1021, "một nghìn không trăm hai mươi mốt"),
    (1100, "một nghìn một trăm"),
    (1234, "một nghìn hai trăm ba mươi tư"),
    (2026, "hai nghìn không trăm hai mươi sáu"),
    (10000, "mười nghìn"),
    (15000, "mười lăm nghìn"),
    (20001, "hai mươi nghìn không trăm linh một"),
    (21000, "hai mươi mốt nghìn"),
    (105000, "một trăm linh năm nghìn"),
    (1000000, "một triệu"),
    (1000001, "một triệu không trăm linh một"),
    (1050000, "một triệu không trăm năm mươi nghìn"),
    (1234567, "một triệu hai trăm ba mươi tư nghìn năm trăm sáu mươi bảy"),
    (1000000000, "một tỷ"),
    (1000000005, "một tỷ không trăm linh năm"),
    (1005000000, "một tỷ không trăm linh năm triệu"),
    (2500000000, "hai tỷ năm trăm triệu"),
    (1000000000000, "một nghìn tỷ"),
    (-7, "âm bảy"),
])
def test_vi_int(n, expected):
    assert vi_int(n) == expected


def test_vi_int_southern_variants():
    assert vi_int(105, zero_tens="lẻ") == "một trăm lẻ năm"
    assert vi_int(1000, thousand="ngàn") == "một ngàn"
    norm = Normalizer(NormalizerConfig(zero_tens="lẻ", thousand="ngàn"))
    assert norm.vi("Có 1005 lệnh.") == "Có một ngàn không trăm lẻ năm lệnh."


# --- separators and decimals ----------------------------------------------------------------

@pytest.mark.parametrize("token,expected", [
    ("12", ("12", "")),
    ("3,5", ("3", "5")),
    ("5.02", ("5", "02")),
    ("1.250", ("1250", "")),
    ("1,000", ("1000", "")),
    ("1.234.567", ("1234567", "")),
    ("1,234,567", ("1234567", "")),
    ("1.234,56", ("1234", "56")),
    ("1,234.56", ("1234", "56")),
    ("0.500", ("0", "500")),
    ("192.168.1.10", None),
])
def test_parse_number(token, expected):
    assert parse_number(token) == expected


@pytest.mark.parametrize("text,expected", [
    ("3,5", "ba phẩy năm"),
    ("3.5", "ba phẩy năm"),
    ("5,02", "năm phẩy không hai"),
    ("0,5", "không phẩy năm"),
    ("4,6", "bốn phẩy sáu"),
    ("3,14", "ba phẩy mười bốn"),
    ("21,5", "hai mươi mốt phẩy năm"),
    ("3,14159", "ba phẩy một bốn một năm chín"),
    ("1.234.567", "một triệu hai trăm ba mươi tư nghìn năm trăm sáu mươi bảy"),
    ("1,000,000", "một triệu"),
    ("12.500.000", "mười hai triệu năm trăm nghìn"),
])
def test_numbers_in_text(text, expected):
    assert normalize_vi(text) == expected


# --- percentages and units ------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("30%", "ba mươi phần trăm"),
    ("3,5%", "ba phẩy năm phần trăm"),
    ("100%", "một trăm phần trăm"),
    ("5V", "năm vôn"),
    ("5 V", "năm vôn"),
    ("3,7 V", "ba phẩy bảy vôn"),
    ("5.02V", "năm phẩy không hai vôn"),
    ("4600mV", "bốn nghìn sáu trăm mi li vôn"),
    ("2A", "hai ampe"),
    ("500 mA", "năm trăm mi li ampe"),
    ("60W", "sáu mươi oát"),
    ("1,5 kW", "một phẩy năm ki lô oát"),
    ("20 Wh", "hai mươi oát giờ"),
    ("2500mAh", "hai nghìn năm trăm mi li ampe giờ"),
    ("31°C", "ba mươi mốt độ xê"),
    ("45 km/h", "bốn mươi lăm ki lô mét trên giờ"),
    ("12 km", "mười hai ki lô mét"),
    ("5 m", "năm mét"),
    ("15 cm", "mười lăm xen ti mét"),
    ("8 mm", "tám mi li mét"),
    ("70 kg", "bảy mươi ki lô gam"),
    ("250 g", "hai trăm năm mươi gam"),
    ("4 GB", "bốn gi ga bai"),
    ("512 MB", "năm trăm mười hai mê ga bai"),
    ("64 KB", "sáu mươi tư ki lô bai"),
    ("50 Hz", "năm mươi héc"),
    ("24 kHz", "hai mươi tư ki lô héc"),
    ("90°", "chín mươi độ"),
    ("1.250 mA", "một nghìn hai trăm năm mươi mi li ampe"),
])
def test_units(text, expected):
    assert normalize_vi(text) == expected


def test_unit_letters_need_a_number():
    assert normalize_vi("Vitamin C và mode A") == "Vitamin C và mode A"
    assert normalize_vi("Chế độ 5 mode") == "Chế độ năm mode"


# --- times and dates ------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("14:32", "mười bốn giờ ba mươi hai phút"),
    ("07:05", "bảy giờ năm phút"),
    ("14:00", "mười bốn giờ"),
    ("23:59:30", "hai mươi ba giờ năm mươi chín phút ba mươi giây"),
    ("14h30", "mười bốn giờ ba mươi phút"),
    ("9h", "chín giờ"),
    ("06/10/2026", "ngày sáu tháng mười năm hai nghìn không trăm hai mươi sáu"),
    ("1/4/2026", "ngày một tháng tư năm hai nghìn không trăm hai mươi sáu"),
    ("2026-10-06", "ngày sáu tháng mười năm hai nghìn không trăm hai mươi sáu"),
    ("ngày 6/10", "ngày sáu tháng mười"),
    ("Ngày 25/12/2026", "Ngày hai mươi lăm tháng mười hai năm hai nghìn không trăm hai mươi sáu"),
    ("10/2026", "tháng mười năm hai nghìn không trăm hai mươi sáu"),
    ("tháng 4", "tháng tư"),
    ("1/2 quãng đường", "một phần hai quãng đường"),
])
def test_times_and_dates(text, expected):
    assert normalize_vi(text) == expected


def test_full_sentence_time_and_date():
    assert normalize_vi("Bây giờ là 14:32, thứ 2 ngày 06/10/2026.") == (
        "Bây giờ là mười bốn giờ ba mươi hai phút, thứ hai ngày sáu tháng mười năm "
        "hai nghìn không trăm hai mươi sáu.")


# --- ordinals, ranges, signs, money, codes ------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("thứ 4", "thứ tư"),
    ("Thứ 7", "Thứ bảy"),
    ("lần thứ 1", "lần thứ nhất"),
    ("hạng 1, hạng 2", "hạng nhất, hạng hai"),
    ("20-30%", "hai mươi đến ba mươi phần trăm"),
    ("3V-5V", "ba vôn đến năm vôn"),
    ("14:00-15:30", "mười bốn giờ đến mười lăm giờ ba mươi phút"),
    ("-5°C", "âm năm độ xê"),
    ("Nhiệt độ -5", "Nhiệt độ âm năm"),
    ("50.000đ", "năm mươi nghìn đồng"),
    ("50.000 VND", "năm mươi nghìn đồng"),
    ("50k", "năm mươi nghìn"),
    ("$5", "năm đô la"),
    ("0912345678", "không chín một hai ba bốn năm sáu bảy tám"),
    ("ESP32", "ESP ba hai"),
    ("v2.5.0", "v hai chấm năm chấm không"),
    ("2+2=4", "hai cộng hai bằng bốn"),
    ("3x4", "ba nhân bốn"),
    ("COVID-19", "COVID mười chín"),
])
def test_misc_numbers(text, expected):
    assert normalize_vi(text) == expected


@pytest.mark.parametrize("text,expected", [
    # A dash inside "NUM op NUM = NUM" is a minus sign, not a range.
    ("Kết quả 10 - 3 = 7.", "Kết quả mười trừ ba bằng bảy."),
    ("10-3=7", "mười trừ ba bằng bảy"),
    ("3 - 5 = -2", "ba trừ năm bằng âm hai"),
    ("20-30%", "hai mươi đến ba mươi phần trăm"),      # still a range: no "="
    # "x/y" as a score next to điểm/sao, as a date after a date cue, else a fraction.
    ("Điểm 9/10.", "Điểm chín trên mười."),
    ("4/5 sao", "bốn trên năm sao"),
    ("được 8/10", "được tám trên mười"),
    ("Kỳ nghỉ bắt đầu từ 1/4.", "Kỳ nghỉ bắt đầu từ ngày một tháng tư."),
    ("hạn 3/4", "hạn ngày ba tháng tư"),
    ("1/2 cốc nước", "một phần hai cốc nước"),
])
def test_arithmetic_scores_and_dates(text, expected):
    assert normalize_vi(text) == expected


def test_english_arithmetic_minus():
    assert normalize_en("10 - 3 = 7") == "ten minus three equals seven"
    assert normalize_en("3 - 5 = -2") == "three minus five equals minus two"


# --- abbreviations --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("AI", "ây ai"),
    ("WiFi", "wai phai"),
    ("Wi-Fi", "wai phai"),
    ("wifi", "wai phai"),
    ("USB", "u ét bê"),
    ("OK", "ô kê"),
    ("LED", "lét"),
    ("HUD", "hắt"),
    ("J.A.R.V.I.S", "Giắc vít"),
    ("Jarvis", "Giắc vít"),
    ("Tôi là J.A.R.V.I.S, trợ lý của ngài.", "Tôi là Giắc vít, trợ lý của ngài."),
    ("J.A.R.V.I.S. đang trực tuyến.", "Giắc vít đang trực tuyến."),
    ("Kết nối WiFi ổn định, USB và LED hoạt động OK.",
     "Kết nối wai phai ổn định, u ét bê và lét hoạt động ô kê."),
])
def test_abbreviations(text, expected):
    assert normalize_vi(text) == expected


def test_abbreviation_needs_word_boundary():
    # "ai" (who) stays; "AIR"/"LEDs" are not the abbreviation
    assert normalize_vi("Ai đang gọi AIR?") == "Ai đang gọi AIR?"


def test_abbreviation_override_file(tmp_path):
    path = tmp_path / "abbr.json"
    path.write_text(json.dumps({"vi": {"HUD": "hớt", "MCP": "em xi pi"}, "en": {"HUD": "head up display"}},
                               ensure_ascii=False), encoding="utf-8")
    cfg = NormalizerConfig()
    cfg.load_overrides(str(path))
    norm = Normalizer(cfg)
    assert norm.vi("HUD và MCP") == "hớt và em xi pi"
    assert norm.en("The HUD is on.") == "The head up display is on."


# --- markup, emoji, symbols ----------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("**Đã mở** mặt nạ 😀", "Đã mở mặt nạ"),
    ("# Trạng thái\n- Pin ổn\n- Đèn bật", "Trạng thái. Pin ổn. Đèn bật"),
    ("Dùng `self.mask.open` nhé", "Dùng self.mask.open nhé"),
    ("Xem [tài liệu](https://example.com/docs) ở đây", "Xem tài liệu ở đây"),
    ("Mở tại https://example.com ngay", "Mở tại ngay"),
    ("Pin & đèn", "Pin và đèn"),
    ("on/off", "on off"),
    ("Xong (thử nghiệm) - được!!!", "Xong, thử nghiệm, được!"),
    ("🔥🔥 Sẵn sàng ✅", "Sẵn sàng"),
    ("_nghiêng_ và open_mask", "nghiêng và open mask"),
])
def test_markup_and_symbols(text, expected):
    assert normalize_vi(text) == expected


def test_emoji_only_becomes_empty():
    assert normalize_vi("😀🎉✨") == ""
    assert strip_markup("👍") == ""


def test_vietnamese_letters_untouched():
    text = "Ưu tiên đường ống, nghiêng ngả, quả thật là khỏe."
    assert normalize_vi(text) == text


# --- English --------------------------------------------------------------------------------

@pytest.mark.parametrize("n,expected", [
    (0, "zero"), (5, "five"), (13, "thirteen"), (21, "twenty-one"), (105, "one hundred and five"),
    (1250, "one thousand two hundred and fifty"), (2500000, "two million five hundred thousand"),
])
def test_en_int(n, expected):
    assert en_int(n) == expected


@pytest.mark.parametrize("text,expected", [
    ("The battery is at 85%.", "The battery is at eighty five percent."),
    ("Voltage 4.6 V, 1 V.", "Voltage four point six volts, one volt."),
    ("It is 14:32.", "It is fourteen thirty two."),
    ("At 09:05 or 9:00.", "At nine oh five or nine o'clock."),
    ("Minus: -5°C.", "Minus: minus five degrees Celsius."),
    ("Born 06/10/2026.", "Born the sixth of October, twenty twenty six."),
    ("I am J.A.R.V.I.S.", "I am Jarvis."),
    ("Load 1,250 mA.", "Load one thousand two hundred and fifty milliamps."),
    ("Since 1999.", "Since nineteen ninety nine."),
    ("Speed 45 km/h.", "Speed forty five kilometres per hour."),
])
def test_normalize_en(text, expected):
    assert normalize_en(text) == expected


# --- English-sentence detection -------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Good evening, sir. All systems are online.", True),
    ("The mask is now open and the eyes are at full brightness.", True),
    ("I'm afraid I can't do that, sir.", True),
    ("Combat mode activated.", True),
    ("Yes sir.", True),
    ("open mask", True),
    ("Đã kích hoạt combat mode, thưa ngài.", False),
    ("Stealth mode engaged, đèn mắt đã tắt.", False),
    ("Mở mặt nạ", False),
    ("anh co khoe khong", False),
    ("toi la tro ly ao cua ngai", False),
    ("OK.", False),
    ("Hello", False),
    ("Jarvis online", False),
    ("USB LED HUD.", False),
    ("12345", False),
    ("", False),
])
def test_is_english_sentence(text, expected):
    assert is_english_sentence(text) is expected


@pytest.mark.parametrize("word,expected", [
    ("nghieng", True), ("khong", True), ("quay", True), ("gia", True), ("the", True),
    ("mode", False), ("is", False), ("system", False), ("sir", False), ("wifi", False),
])
def test_looks_vietnamese_syllable(word, expected):
    assert looks_vietnamese_syllable(word) is expected


def test_has_vietnamese_marks():
    assert has_vietnamese_marks("đèn")
    assert has_vietnamese_marks("Ở")
    assert not has_vietnamese_marks("Good evening")


# --- sentence splitting used for routing ---------------------------------------------------

def test_split_sentences_for_routing():
    text = "Xin chào. Good evening, sir. Pin còn 5.02 V. Tôi là J.A.R.V.I.S. Rất vui!\nHết"
    assert split_sentences(text) == ["Xin chào.", "Good evening, sir.", "Pin còn 5.02 V.",
                                     "Tôi là J.A.R.V.I.S.", "Rất vui!", "Hết"]


def test_split_sentences_keeps_abbreviations():
    assert split_sentences("Gặp TS. Nguyễn ở TP. Hồ Chí Minh. Xong.") == [
        "Gặp TS. Nguyễn ở TP. Hồ Chí Minh.", "Xong."]


# --- whole bench sentences -----------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Điện áp nguồn hiện tại là 5,02 V, dòng tiêu thụ 1.250 mA.",
     "Điện áp nguồn hiện tại là năm phẩy không hai vôn, dòng tiêu thụ một nghìn hai trăm năm mươi mi li ampe."),
    ("Pin còn 3.7V và 2500mAh, đủ dùng khoảng 2 giờ nữa.",
     "Pin còn ba phẩy bảy vôn và hai nghìn năm trăm mi li ampe giờ, đủ dùng khoảng hai giờ nữa."),
    ("Nhiệt độ bên ngoài là 31°C, độ ẩm 75%.",
     "Nhiệt độ bên ngoài là ba mươi mốt độ xê, độ ẩm bảy mươi lăm phần trăm."),
    ("Tốc độ hiện tại 45 km/h, quãng đường còn lại 12,5 km.",
     "Tốc độ hiện tại bốn mươi lăm ki lô mét trên giờ, quãng đường còn lại mười hai phẩy năm ki lô mét."),
    ("Bộ nhớ còn trống 512 MB trên tổng số 4 GB.",
     "Bộ nhớ còn trống năm trăm mười hai mê ga bai trên tổng số bốn gi ga bai."),
    ("Tần số lấy mẫu là 24 kHz, tức là 24.000 Hz.",
     "Tần số lấy mẫu là hai mươi tư ki lô héc, tức là hai mươi tư nghìn héc."),
])
def test_bench_sentences(text, expected):
    assert normalize_vi(text) == expected
