#!/usr/bin/env python3
"""
text.py — Алгоритм схожести текстов без внешних зависимостей.

Использует:
  1. Косинусную близость символьных 3-грамм (char 3-grams) — устойчивость к опечаткам и морфологии.
  2. Коэффициент Жаккара по стеммированным словам — устойчивость к перестановке слов и падежам.
  3. Гардрайлы отрицаний и полярности команд (не/нет, включить/выключить, create/delete) — защита от ложных совпадений с противоположным смыслом.
  4. Очистку от разговорных стоп-слов-паразитов ("подскажи пожалуйста", "как", "what is").
"""

import re
import math
from collections import Counter
from typing import Set, List, Dict, Tuple, Any

# Разговорные и грамматические стоп-слова (предлоги, союзы, частицы), не несущие смысловой нагрузки
CONVERSATIONAL_STOP_WORDS: Set[str] = {
    # Russian разговорные
    "подскажи", "пожалуйста", "скажи", "привет", "здравствуй", "здравствуйте",
    "помоги", "хочу", "узнать", "как", "что", "такое", "зачем", "почему",
    "где", "когда", "кто", "мне", "нам", "нужно", "надо", "можно", "ли",
    "быстро", "правильно", "лучше", "подскажите", "вопрос", "ответ",
    # Russian предлоги и союзы
    "в", "во", "на", "с", "со", "по", "из", "изо", "к", "ко", "о", "об", "обо",
    "у", "за", "под", "над", "при", "про", "через", "для", "от", "до",
    "и", "или", "а", "но", "да",
    # English conversational
    "hello", "hi", "hey", "please", "tell", "me", "how", "to", "what", "is",
    "why", "where", "when", "who", "can", "could", "would", "should", "you",
    "help", "want", "know", "need", "best", "way",
    # English prepositions & conjunctions
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "about",
    "into", "over", "after", "and", "or", "but", "a", "an", "the"
}

# Критические слова отрицания
NEGATION_WORDS: Set[str] = {
    # Russian
    "не", "нет", "без", "нельзя", "запрещено", "никогда", "нигде", "никак",
    # English
    "not", "no", "never", "without", "none", "neither", "nor", "cannot", "dont", "cant"
}

# Пары взаимно исключающих антонимов (команд/действий)
ANTONYM_PAIRS: List[Tuple[Set[str], Set[str]]] = [
    # Russian
    ({"включить", "включи", "включение", "вкл", "подключить"},
     {"выключить", "выключи", "выключение", "выкл", "отключить"}),
    ({"создать", "создай", "добавить", "добавь"},
     {"удалить", "удали", "стереть", "уничтожить"}),
    ({"разрешить", "разреши", "включить"},
     {"запретить", "запрети", "заблокировать"}),
    ({"начать", "начни", "запустить", "старт"},
     {"остановить", "останови", "завершить", "стоп"}),
    ({"купить", "покупка"},
     {"продать", "продажа"}),
    # English
    ({"enable", "start", "turnon", "activate"},
     {"disable", "stop", "turnoff", "deactivate"}),
    ({"create", "add"},
     {"delete", "remove", "drop"}),
    ({"allow", "permit"},
     {"deny", "block", "forbid"}),
]

RU_ENDINGS_REGEX = re.compile(
    r"(?:[аеёиоуыэюя]|[ыиое]й|[ая]я|[ое]е|[ыи]е|[ыиое]х|[ыи]м|[ыи]ми|[ое]го|[ое]му|[ое]в|[её]й|[ео]м|[ая]ми|ть|ти|ла|ло|ли|ем|им|ут|ют|ат|ят|ешь|ишь|ся|сь)$"
)


def normalize_string(text: str) -> str:
    """Приводит строку к нижнему регистру, убирает апострофы и нормализует пробелы."""
    text = text.lower()
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^\w\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_words(text: str) -> List[str]:
    """Извлекает список слов из нормализованного текста."""
    return re.findall(r"\b[\w-]+\b", text.lower())


def stem_word(w: str) -> str:
    """Лёгкий стеммер для русского и английского языков без внешних библиотек."""
    w = w.lower()
    if len(w) <= 3:
        return w
    # Английские окончания (не отсекаем s после s: pass, class; сохраняем основу >= 3 символов)
    w_en = re.sub(r"(?:ing|ed|es|(?<!s)s)$", "", w)
    if len(w_en) >= 3:
        w = w_en
    # Русские окончания (в 2 прохода для составных суффиксов)
    for _ in range(2):
        w_sub = RU_ENDINGS_REGEX.sub("", w)
        if len(w_sub) >= 3:
            w = w_sub
    return w


def get_negations(words: List[str]) -> Set[str]:
    """Возвращает множество найденных отрицаний."""
    return {w for w in words if w in NEGATION_WORDS}


def check_antonym_conflict(words1: List[str], words2: List[str]) -> bool:
    """
    Возвращает True, если в одном тексте используется полярная команда,
    а в другом — противоположная (при условии, что ни один текст не сравнивает обе).
    """
    set1, set2 = set(words1), set(words2)
    for group_a, group_b in ANTONYM_PAIRS:
        has_a1 = bool(set1 & group_a)
        has_b1 = bool(set1 & group_b)
        has_a2 = bool(set2 & group_a)
        has_b2 = bool(set2 & group_b)
        
        # Если в каком-либо из текстов присутствуют оба термина (сравнение) — конфликта нет
        if (has_a1 and has_b1) or (has_a2 and has_b2):
            continue
        
        # Конфликт только когда текст 1 строго про group_a, а текст 2 строго про group_b (или наоборот)
        if (has_a1 and has_b2) or (has_b1 and has_a2):
            return True
    return False


def filter_meaningful_words(words: List[str]) -> List[str]:
    """Удаляет разговорные стоп-слова, сохраняя смысловые термины и отрицания."""
    return [w for w in words if w not in CONVERSATIONAL_STOP_WORDS]


def char_ngrams(text: str, n: int = 3) -> Counter:
    """Генерирует мультимножество символьных n-грамм с граничными пробелами."""
    cleaned = f" {text.strip()} "
    if len(cleaned) < n:
        return Counter([cleaned])
    ngrams = [cleaned[i:i + n] for i in range(len(cleaned) - n + 1)]
    return Counter(ngrams)


def cosine_similarity(vec1: Counter, vec2: Counter) -> float:
    """Вычисляет косинусную близость двух счетчиков (векторов)."""
    if not vec1 or not vec2:
        return 0.0
    
    intersection = set(vec1.keys()) & set(vec2.keys())
    dot_product = sum(vec1[k] * vec2[k] for k in intersection)
    
    norm1 = math.sqrt(sum(v ** 2 for v in vec1.values()))
    norm2 = math.sqrt(sum(v ** 2 for v in vec2.values()))
    
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    
    return dot_product / (norm1 * norm2)


def jaccard_similarity(words1: List[str], words2: List[str]) -> float:
    """Вычисляет коэффициент Жаккара по стеммированным множествам слов."""
    stems1 = {stem_word(w) for w in words1}
    stems2 = {stem_word(w) for w in words2}
    
    if not stems1 and not stems2:
        return 1.0
    if not stems1 or not stems2:
        return 0.0
    intersection = len(stems1 & stems2)
    union = len(stems1 | stems2)
    return intersection / union if union > 0 else 0.0


def calculate_similarity(text1: str, text2: str) -> Tuple[float, Dict[str, Any]]:
    """
    Вычисляет комбинированную схожесть двух текстов (0.0 .. 1.0).
    
    Включает:
      - Проверку на точное совпадение (1.0).
      - Гардрайл отрицаний (не/нет/без).
      - Гардрайл взаимно исключающих команд (включить vs выключить).
      - Символьные 3-граммы (косинус) для устойчивости к опечаткам.
      - Жаккар со стеммингом для учёта морфологии и порядка слов.
    """
    t1_norm = normalize_string(text1)
    t2_norm = normalize_string(text2)
    
    # 1. Точное совпадение
    if t1_norm == t2_norm:
        return 1.0, {"exact": True, "cosine_3gram": 1.0, "jaccard_words": 1.0, "combined": 1.0}
    
    words1 = extract_words(t1_norm)
    words2 = extract_words(t2_norm)
    
    if not words1 or not words2:
        return 0.0, {"reason": "empty_input", "combined": 0.0}
    
    # 2. Гардрайл отрицаний: если в одном есть отрицание, а в другом нет
    neg1 = get_negations(words1)
    neg2 = get_negations(words2)
    if bool(neg1) != bool(neg2):
        return 0.0, {
            "conflict": "negation_mismatch",
            "negations1": list(neg1),
            "negations2": list(neg2),
            "combined": 0.0
        }
    
    # 3. Гардрайл полярности/антонимов ("включить" vs "выключить")
    if check_antonym_conflict(words1, words2):
        return 0.0, {
            "conflict": "antonym_command_mismatch",
            "combined": 0.0
        }
    
    # 4. Фильтрация разговорного мусора
    core_words1 = filter_meaningful_words(words1)
    core_words2 = filter_meaningful_words(words2)
    
    eff_words1 = core_words1 if core_words1 else words1
    eff_words2 = core_words2 if core_words2 else words2
    
    core_text1 = " ".join(eff_words1)
    core_text2 = " ".join(eff_words2)
    
    # 5. Косинусная близость символьных 3-грамм
    vec1 = char_ngrams(core_text1, n=3)
    vec2 = char_ngrams(core_text2, n=3)
    cosine_score = cosine_similarity(vec1, vec2)
    
    # 6. Коэффициент Жаккара со стеммингом
    jaccard_score = jaccard_similarity(eff_words1, eff_words2)
    
    # 7. Комбинированный скор
    combined = 0.6 * cosine_score + 0.4 * jaccard_score
    
    details = {
        "cosine_3gram": round(cosine_score, 4),
        "jaccard_words": round(jaccard_score, 4),
        "combined": round(combined, 4)
    }
    
    return round(combined, 4), details


def is_similar(text1: str, text2: str, threshold: float = 0.88) -> Tuple[bool, float, Dict[str, Any]]:
    """Проверяет, превышает ли схожесть заданный порог (по умолчанию 0.88)."""
    score, details = calculate_similarity(text1, text2)
    matched = score >= threshold
    return matched, score, details


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Использование: python text.py <текст1> <текст2> [порог]")
        sys.exit(1)
    
    thresh = float(sys.argv[3]) if len(sys.argv) > 3 else 0.88
    matched, score, det = is_similar(sys.argv[1], sys.argv[2], threshold=thresh)
    status = "СОВПАДЕНИЕ (HIT)" if matched else "НЕ СОВПАДАЕТ (MISS)"
    print(f"Схожесть: {score:.4f} (порог {thresh}) -> {status}")
    print(f"Детали: {det}")
