"""Emoji / kaomoji / symbol data.

Emoji come from Plasma's own emoji dictionaries (/usr/share/plasma/emoji/<lang>.dict, the
same data the Plasma Emoji Selector uses), so names and search tags are localized.
"""
import locale
import os
import struct
import zlib

PLASMA_EMOJI_DIR = "/usr/share/plasma/emoji"
SKIN_TONES = ["", "\U0001F3FB", "\U0001F3FC", "\U0001F3FD", "\U0001F3FE", "\U0001F3FF"]

# Plasma's category ids -> (icon, english name, vietnamese name)
EMOJI_CATEGORIES = [
    (1, "😀", "Smileys & emotion", "Mặt cười & cảm xúc"),
    (2, "👋", "People & body", "Con người & cơ thể"),
    (4, "🐻", "Animals & nature", "Động vật & thiên nhiên"),
    (5, "🍔", "Food & drink", "Đồ ăn & thức uống"),
    (6, "✈️", "Travel & places", "Du lịch & địa điểm"),
    (7, "⚽", "Activities", "Hoạt động"),
    (8, "💡", "Objects", "Đồ vật"),
    (9, "🔣", "Symbols", "Biểu tượng"),
    (10, "🏁", "Flags", "Cờ"),
]


class Emoji:
    __slots__ = ("char", "name", "category", "search", "tones")

    def __init__(self, char, name, category, search):
        self.char, self.name, self.category, self.search = char, name, category, search
        self.tones = {}  # skin tone modifier -> variant char


def _read_dict(path):
    with open(path, "rb") as fh:
        raw = zlib.decompress(fh.read()[4:])  # qCompress: 4-byte length + zlib stream
    off = 0

    def i32():
        nonlocal off
        val = struct.unpack_from("<i", raw, off)[0]
        off += 4
        return val

    def s():
        nonlocal off
        n = i32()
        val = raw[off:off + n].decode("utf-8")
        off += n
        return val

    out = []
    for _ in range(i32()):
        char, name, cat = s(), s(), i32()
        tags = [s() for _ in range(i32())]
        out.append((char, name, cat, tags))
    return out


def _lang_candidates(lang=None):
    lang = lang or os.environ.get("LANGUAGE", "").split(":")[0] or os.environ.get("LC_MESSAGES") \
        or os.environ.get("LANG") or (locale.getlocale()[0] or "")
    lang = lang.split(".")[0]
    return [c for c in (lang, lang.split("_")[0]) if c and c not in ("C", "POSIX", "en")]


def load_emoji(lang=None):
    """Return a list of Emoji (skin-tone variants folded into their base emoji)."""
    en_path = os.path.join(PLASMA_EMOJI_DIR, "en.dict")
    try:
        base = _read_dict(en_path)
    except (OSError, zlib.error, struct.error):
        return _fallback_emoji()
    local = {}
    for lang in _lang_candidates(lang):
        path = os.path.join(PLASMA_EMOJI_DIR, f"{lang}.dict")
        if os.path.exists(path):
            try:
                local = {c: (n, t) for c, n, _cat, t in _read_dict(path)}
            except (OSError, zlib.error, struct.error):
                pass
            break

    result, by_name = [], {}
    for char, name, cat, tags in base:
        if any(t in char for t in SKIN_TONES[1:]):
            # "waving hand: light skin tone" -> attach to "waving hand"
            if ": " in name:
                parent = by_name.get(name.split(": ")[0])
                tone = next(t for t in SKIN_TONES[1:] if t in char)
                if parent is not None and "," not in name.split(": ")[1]:
                    parent.tones.setdefault(tone, char)
            continue
        lname, ltags = local.get(char, (None, []))
        display = lname or name
        search = " ".join([name, lname or "", *tags, *ltags]).lower()
        emo = Emoji(char, display, cat, search)
        by_name[name] = emo
        result.append(emo)
    return result


def _fallback_emoji():
    chars = "😀😃😄😁😆😅🤣😂🙂🙃😉😊😇🥰😍🤩😘😋😛😜🤪🤗🤔🤐😐😑😶😏😒🙄😬😌😔😪😴😷🤒🥵🥶😵🤯🥳😎🤓😕😟🙁😮😲😳🥺😦😧😨😰😥😢😭😱😖😣😞😓😩😫🥱😤😡😠🤬👍👎👏🙏💪❤️🧡💛💚💙💜🖤🤍💯🔥✨🎉✅❌⭐"
    import unicodedata
    out = []
    for ch in chars:
        if unicodedata.category(ch) == "Mn" or ch == "️":
            continue
        name = unicodedata.name(ch, "emoji").lower()
        out.append(Emoji(ch, name, 1, name))
    return out


# ---------------------------------------------------------------- kaomoji
KAOMOJI = {
    "Chào ・ Greeting": [
        "(^_^)/", "(｀・ω・´)ゞ", "ヾ(・ω・*)", "(*・ω・)ﾉ", "( ´ ▽ ` )ﾉ", "(￣▽￣)ノ", "ヾ(^∇^)",
        "(⌒▽⌒)☆", "(o´▽`o)ﾉ", "ヽ(・∀・)ﾉ", "(＾▽＾)/", "ヾ(＾-＾)ノ",
    ],
    "Vui ・ Happy": [
        "(◕‿◕)", "(＾▽＾)", "(✿◠‿◠)", "(*^‿^*)", "(≧◡≦)", "ヽ(>∀<☆)ノ", "(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧",
        "＼(٥⁀▽⁀ )／", "(´｡• ᵕ •｡`)", "( ˙▿˙ )", "(o^▽^o)", "٩(◕‿◕｡)۶", "☆*:.｡.o(≧▽≦)o.｡.:*☆",
        "(๑˃ᴗ˂)ﻭ", "(ᵔᴥᵔ)", "ʘ‿ʘ", "(＾◡＾)", "(⁀ᗢ⁀)", "( ´ ∀ ` )", "(•‿•)",
    ],
    "Yêu ・ Love": [
        "(♥ω♥*)", "(｡♥‿♥｡)", "(´∀｀)♡", "(づ￣ ³￣)づ", "(っ˘з(˘⌣˘ )", "♡(˘▽˘>ԅ( ˘⌣˘)",
        "(*˘︶˘*).｡.:*♡", "( ˘ ³˘)♥", "(´ε｀ )♡", "(≧◡≦) ♡", "(◍•ᴗ•◍)❤", "ღゝ◡╹)ノ♡",
    ],
    "Buồn ・ Sad": [
        "(╥﹏╥)", "(ಥ﹏ಥ)", "(T_T)", "(;_;)", "(｡•́︿•̀｡)", "(っ˘̩╭╮˘̩)っ", "( ╥ω╥ )",
        "(◞‸◟；)", "(ノ_<。)", "｡ﾟ(ﾟ´Д｀ﾟ)ﾟ｡", "(´；ω；`)", "(-_-;)",
    ],
    "Giận ・ Angry": [
        "(╬ Ò﹏Ó)", "(ノಠ益ಠ)ノ彡┻━┻", "(＃`Д´)", "(ಠ_ಠ)", "(｀皿´＃)", "ヽ(`⌒´メ)ノ",
        "(凸ಠ益ಠ)凸", "(╯°□°)╯︵ ┻━┻", "┬─┬ノ( º _ ºノ)", "(ง •̀_•́)ง", "(¬_¬)", "(눈_눈)",
    ],
    "Ngạc nhiên ・ Surprise": [
        "(⊙_⊙)", "(°o°)", "Σ(°△°|||)", "(O_O)", "w(°ｏ°)w", "(□_□)", "(゜゜)", "ヽ(°〇°)ﾉ",
        "(*゜ロ゜)ノ", "(ﾟДﾟ;)",
    ],
    "Khác ・ Other": [
        "¯\\_(ツ)_/¯", "( ͡° ͜ʖ ͡°)", "(⌐■_■)", "ʕ•ᴥ•ʔ", "(=^･ω･^=)", "ฅ^•ﻌ•^ฅ", "(・ω・)",
        "(￣ー￣)", "(¬‿¬)", "(҂◡_◡)", "(-_-)zzz", "( ̄o ̄) . z Z", "ᕦ(ò_óˇ)ᕤ", "(•_•)",
        "( •_•)>⌐■-■", "(☞ﾟヮﾟ)☞", "☜(ﾟヮﾟ☜)", "(ﾉ´ヮ`)ﾉ*: ･ﾟ", "♪~ ᕕ(ᐛ)ᕗ", "(っ◔◡◔)っ",
        "＿φ(°-°=)", "m(_ _)m", "(シ_ _)シ", "(´-ω-`)",
    ],
}


# ---------------------------------------------------------------- symbols
def _rng(a, b, skip=()):
    return [chr(c) for c in range(a, b + 1) if c not in skip]


SYMBOLS = {
    "Dấu câu ・ Punctuation": list("…–—‘’“”‚„«»‹›¡¿·•‣※‼⁇⁈⁉§¶†‡′″‴〃°№™©®℗℠"),
    "Tiền tệ ・ Currency": list("₫$€£¥¢₩₹₽₺₴₦₱₲₪₡₵₸₿฿₭₮¤"),
    "Toán học ・ Math": list("±×÷≠≈≡≤≥≪≫∞√∛∜∑∏∫∬∮∂∆∇∈∉∋∌∩∪⊂⊃⊆⊇∧∨¬∀∃∄∅∝∠∟⊥∥∴∵⌀‰‱¼½¾⅓⅔⅛⅜⅝⅞"),
    "Mũi tên ・ Arrows": _rng(0x2190, 0x21FF) + list("➔➘➙➚➛➜➝➞➟➠➡➢➣➤➥➦➧➨⬅⬆⬇⬈⬉⬊⬋"),
    "Chỉ số ・ Super/Subscript": list("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₔₕₖₗₘₙₚₛₜ"),
    "Hy Lạp ・ Greek": _rng(0x0391, 0x03A9, skip=(0x03A2,)) + _rng(0x03B1, 0x03C9),
    "Hình ・ Shapes": list("■□▢▣▤▥▦▧▨▩▪▫▬▭▮▯▰▱▲△▴▵▶▷▸▹►▻▼▽▾▿◀◁◂◃◄◅◆◇◈◉◊○◌◍◎●◐◑◒◓◔◕◖◗◘◙◢◣◤◥◦◧◨◩◪◫◬◭◮◯★☆✦✧✩✪✫✬✭✮✯✰"),
    "Khung ・ Box drawing": _rng(0x2500, 0x257F),
    "Khác ・ Misc": list("✓✔✕✖✗✘☐☑☒♠♡♢♣♤♥♦♧♩♪♫♬♭♮♯☀☁☂☃☄☎☏☘☚☛☜☝☞☟☠☢☣☮☯☸☹☺☻☼☽☾♀♂⚐⚑⚠⚡⌘⌥⇧⌃⎋⏎⌫⌦⏏⏻⏼⏽"),
    "Latinh ・ Latin": list("ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿĂăĐđĨĩŨũƠơƯưẠạẢảẤấẦầẨẩẪẫẬậẮắẰằẲẳẴẵẶặẸẹẺẻẼẽẾếỀềỂểỄễỆệỈỉỊịỌọỎỏỐốỒồỔổỖỗỘộỚớỜờỞởỠỡỢợỤụỦủỨứỪừỬửỮữỰựỲỳỴỵỶỷỸỹ"),
}
