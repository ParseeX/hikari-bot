"""OURYGO 分享码与可热更新的卡组标签规则，无网络请求。"""
import base64
import json
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlencode

from .replay import Deck


def deck_url(deck: Deck) -> str:
    """OURYGO v1：8/4/4 位种类数，各卡 2 位数量和 27 位卡号。"""
    sections = [Counter(deck.main), Counter(deck.extra), Counter(deck.side)]
    counts = [len(section) for section in sections]
    if counts[0] > 255 or counts[1] > 15 or counts[2] > 15:
        raise ValueError("卡组超出分享码容量")
    bits = f"{counts[0]:08b}{counts[1]:04b}{counts[2]:04b}"
    for section in sections:
        for card, count in sorted(section.items()):
            if not 0 < card < 2**27 or not 1 <= count <= 3:
                raise ValueError("卡号或数量超出分享码容量")
            bits += f"{count:02b}{card:027b}"
    bits += "0" * (-len(bits) % 8)
    payload = int(bits, 2).to_bytes(len(bits) // 8, "big")
    code = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    query = urlencode({"ygotype": "deck", "v": "1", "d": code,
                       "name": f"{deck.player}（Side不完整）"}, quote_via=quote)
    # 站点目前只提供 HTTP，不能擅自改成未开放的 HTTPS。
    return "http://deck.ourygo.top/?" + query


def read_rules(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("rules"), list):
        raise ValueError("规则格式需要 version: 1 和 rules 数组")
    for rule in data["rules"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("label"), str) or not rule["label"].strip():
            raise ValueError("每条规则需要非空 label")
        cards = rule.get("cards")
        if not isinstance(cards, list) or not cards or any(type(c) is not int or c <= 0 for c in cards):
            raise ValueError("cards 必须是非空的正整数卡号数组")
        if rule.get("match", "any") not in ("any", "all"):
            raise ValueError("match 只能是 any 或 all")
    return data["rules"]


def classify(deck: Deck, rules: list[dict]) -> dict:
    present = set(deck.main + deck.extra)
    labels, evidence = [], []
    for rule in rules:
        required = set(rule["cards"])
        matched = sorted(present & required)
        hit = required <= present if rule.get("match", "any") == "all" else bool(matched)
        if hit:
            label = rule["label"].strip()
            if label not in labels:
                labels.append(label)
            evidence.append({"label": label, "cards": matched})
    return {"name": "＋".join(labels) if labels else "未识别", "labels": labels, "evidence": evidence}


def consolidated_decks(replays: list[dict]) -> list[Deck]:
    """以最早收到的录像为基准，按各小局相对基准的最大正差推断 Side。"""
    players = {}
    for replay in sorted(replays, key=lambda r: r.get("start_time", 0)):
        for raw in replay.get("decks", []):
            players.setdefault(raw["player"], []).append(raw)
    result = []
    for player, rounds in players.items():
        base = rounds[0]
        base_counts = Counter(base["main"] + base["extra"])
        side = Counter()
        for later in rounds[1:]:
            # Counter 的正差保留数量，| 取最大值，不重复累加每局换入的同一张卡。
            side |= Counter(later["main"] + later["extra"]) - base_counts
        result.append(Deck(player, tuple(sorted(base["main"])), tuple(sorted(base["extra"])),
                           tuple(sorted(side.elements()))))
    return result


def build_messages(match_id: str, players: list[str], replays: list[dict], issues: list[str],
                   complete: bool, rules: list[dict]) -> list[str]:
    """每位玩家展示基准卡组加已观察 Side，全部小局原件另行保留。"""
    state = "对局已结束" if complete else "对局归档不完整"
    messages = [f"MyCard {state}\n{' vs '.join(players)}\n"
                f"归档编号：{match_id}\n收到 {len(replays)} 份录像。以最早录像为基准；Side 仅含后来观察到的换入卡，不完整。"]
    for deck in consolidated_decks(replays):
        line = (f"{deck.player}\n卡组：{classify(deck, rules)['name']}\n"
                f"主卡 {len(deck.main)} / 额外 {len(deck.extra)} / 已观察 Side {len(deck.side)}（不完整）\n")
        try:
            line += deck_url(deck)
        except ValueError:
            line += "此卡组不能编码为 OURYGO 链接，原始 YDK 已保留。"
        messages.append(line)
    if issues:
        messages.append("采集说明：" + "；".join(dict.fromkeys(issues)))
    messages.append(f"原始文件：私聊发送 MyCard文件 {match_id}")
    # QQ 长文本分段，保持 URL 完整且玩家名/标签作为纯文本发送。
    chunks = [messages[0]]
    for message in messages[1:]:
        if len(chunks[-1]) + len(message) + 2 <= 1800:
            chunks[-1] += "\n\n" + message
        else:
            chunks.append(message)
    return chunks
