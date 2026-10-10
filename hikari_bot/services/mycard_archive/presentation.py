"""OURYGO 分享码与可热更新的卡组标签规则，无网络请求。"""
import base64
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlencode

from .replay import Deck
from .sorting import sort_sections


def deck_url(deck: Deck) -> str:
    """OURYGO v1：8/4/4 位种类数，各卡 2 位数量和 27 位卡号。"""
    sections = [Counter(section) for section in sort_sections(deck.main, deck.extra, deck.side)]
    counts = [len(section) for section in sections]
    if counts[0] > 255 or counts[1] > 15 or counts[2] > 15:
        raise ValueError("卡组超出分享码容量")
    bits = f"{counts[0]:08b}{counts[1]:04b}{counts[2]:04b}"
    for section in sections:
        for card, count in section.items():
            if not 0 < card < 2**27 or not 1 <= count <= 3:
                raise ValueError("卡号或数量超出分享码容量")
            bits += f"{count:02b}{card:027b}"
    bits += "0" * (-len(bits) % 8)
    payload = int(bits, 2).to_bytes(len(bits) // 8, "big")
    code = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    query = urlencode({"ygotype": "deck", "v": "1", "d": code,
                       "name": deck.player}, quote_via=quote)
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


def failure_reason(issues: list[str], replays: list[dict]) -> str:
    """只输出一个简短原因；完整诊断留在归档中，不拼接到私聊。"""
    if any(not replay.get("decks") for replay in replays):
        return "ReplayParseError（录像解析失败）"
    reasons = (
        ("入场冷却", "ServerUnavailable（观战入口暂不可用）"),
        ("等待观战名额", "QueueFull（排队时对局已结束）"),
        ("排名查询失败", "RankLookupError（排名查询失败）"),
        ("禁止观战", "WatchDenied（房间禁止观战）"),
        ("账号正在参与", "AccountInDuel（观战账号正在对局）"),
        ("认证或观战被拒绝", "AuthError（认证或观战被拒绝）"),
        ("重启后", "Interrupted（采集进程重启）"),
        ("连接中断", "ConnectionClosed（观战连接断开）"),
    )
    for issue in reversed(issues):
        match = re.fullmatch(r"观战异常：([A-Za-z][A-Za-z0-9_]*Error)", issue)
        if match:
            kind = match[1]
            detail = {"ConnectionResetError": "观战连接被重置", "TimeoutError": "观战连接超时",
                      "HandshakeTimeoutError": "观战入场无响应"}.get(kind)
            return kind + (f"（{detail}）" if detail else "")
        for text, reason in reasons:
            if text in issue:
                return reason
    return "NoReplay（未收到录像）"


def build_messages(match_id: str, players: list[str], replays: list[dict], issues: list[str],
                   complete: bool, rules: list[dict]) -> list[str]:
    """私聊仅包含双方名字、卡组，或简短的提取失败原因。"""
    messages = [' vs '.join(players)]
    decks = {deck.player: deck for deck in consolidated_decks(replays)}
    if not decks:
        return [messages[0] + "\n提取失败：" + failure_reason(issues, replays)]
    for player in players:
        deck = decks.get(player)
        if deck is None:
            messages.append(f"{player}：提取失败，{failure_reason(issues, replays)}")
            continue
        label = classify(deck, rules)
        line = f"{player}：" + (label['name'] + "\n" if label['labels'] else "")
        try:
            line += deck_url(deck)
        except ValueError:
            line += "提取失败，DeckLinkError（卡组链接生成失败）"
        messages.append(line)
    # QQ 长文本分段，保持 URL 完整且玩家名/标签作为纯文本发送。
    chunks = [messages[0]]
    for message in messages[1:]:
        if len(chunks[-1]) + len(message) + 1 <= 1800:
            chunks[-1] += "\n" + message
        else:
            chunks.append(message)
    return chunks
