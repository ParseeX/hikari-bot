import subprocess
import base64
import io
import os
import re
from typing import OrderedDict
import urllib.parse
import cairosvg
import fitz
import asyncio
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from hikari_bot.utils.ygocard import *

deck_icon_file = os.path.join(RESOURCES_DIR, 'deck_icon.svg')
decklist_template = os.path.join(RESOURCES_DIR, 'deck_cn.pdf')
font_path_sc = os.path.join(RESOURCES_DIR, 'NotoSansCJKsc-Medium.ttf')
font_path_jp = os.path.join(RESOURCES_DIR, 'NotoSansCJKjp-Medium.ttf')
font_path_sc_subset = os.path.join(RESOURCES_DIR, 'sc.ttf')
font_path_jp_subset = os.path.join(RESOURCES_DIR, 'jp.ttf')
log_file = os.path.join(DATA_DIR, 'deck_usage.log')


def base64url_decode(input_str):
    padding = '=' * (4 - len(input_str) % 4)
    return base64.urlsafe_b64decode(input_str + padding)

def parse_deck_data(deck_data):
    main_count = int(deck_data[:8], 2)
    extra_count = int(deck_data[8:12], 2)
    side_count = int(deck_data[12:16], 2)
    
    cards = deck_data[16:]
    main_deck = []
    extra_deck = []
    side_deck = []
    
    index = 0
    for _ in range(main_count):
        qty = int(cards[index:index+2], 2)
        card_id = int(cards[index+2:index+29], 2)
        main_deck.extend([card_id] * qty)
        index += 29
    
    for _ in range(extra_count):
        qty = int(cards[index:index+2], 2)
        card_id = int(cards[index+2:index+29], 2)
        extra_deck.extend([card_id] * qty)
        index += 29
    
    for _ in range(side_count):
        qty = int(cards[index:index+2], 2)
        card_id = int(cards[index+2:index+29], 2)
        side_deck.extend([card_id] * qty)
        index += 29
    
    return main_deck, extra_deck, side_deck

def format_deck_text(main_deck, extra_deck, side_deck):
    deck_text = []
    deck_text.append("#main")
    deck_text.extend(map(str, main_deck))
    deck_text.append("#extra")
    deck_text.extend(map(str, extra_deck))
    deck_text.append("!side")
    deck_text.extend(map(str, side_deck))
    return "\n".join(deck_text)

def get_deck_text_from_url(url):
    """
    从卡组链接生成卡组文本。
    :param url: 卡组链接
    :return: 卡组文本
    """
    # 解析 URL
    parsed_url = urllib.parse.urlparse(url)
    query_params = urllib.parse.parse_qs(parsed_url.query)
    if 'd' not in query_params:
        raise ValueError("URL 中缺少 d 参数")

    d_param = query_params['d'][0]
    decoded_data = base64url_decode(d_param)
    binary_data = ''.join(f'{byte:08b}' for byte in decoded_data)

    # 解析卡组数据
    main_deck, extra_deck, side_deck = parse_deck_data(binary_data)

    # 转换为文本
    return format_deck_text(main_deck, extra_deck, side_deck)

def save_deck_text_as_ydk(deck_text, output_file):
    """
    将卡组文本保存为 .ydk 文件。
    :param deck_text: 卡组文本
    :param output_file: 保存的文件路径
    """
    with open(output_file, 'w') as f:
        f.write(deck_text)

def is_deck_url(url):
    url_pattern = re.compile(r'(ygo|http|https)://[^\s]+')
    url_match = url_pattern.search(url)
    if url_match:
        url = url_match.group(0)
        parsed_url = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        
        return 'ygotype' in query_params and query_params['ygotype'][0] == 'deck' and 'd' in query_params
    return False

def is_deck_code(text):
    deck_identifiers = ['#main', '#extra', '!side']
    return any(identifier in text for identifier in deck_identifiers)

def parse_ydk(deck_text):
    """解析 YDK 文件，返回主卡组、额外卡组和副卡组的卡片列表"""
    main_deck, extra_deck, side_deck = [], [], []
    current_section = None

    for line in deck_text.splitlines():
        line = line.strip()
        if line.startswith("#main"):
            current_section = main_deck
        elif line.startswith("#extra"):
            current_section = extra_deck
        elif line.startswith("!side"):
            current_section = side_deck
        elif line.startswith("#"):
            current_section = None
        elif line and current_section != None:
            current_section.append(line)
    
    return main_deck, extra_deck, side_deck




async def batch_get_images(card_ids, width=200, height=290):
    # 并发下载图片
    tasks = [get_ygopic(card_id) for card_id in card_ids]
    images = await asyncio.gather(*tasks)
    result = []
    for img in images:
        if img is not None:
            result.append(Image.open(BytesIO(img)).resize((width, height)))
    return result


def draw_section(img, card_images, title, start_x, start_y, card_width, card_height, rows, padding):
    draw = ImageDraw.Draw(img)
    cards_per_row = 10

    # 计算布局
    section_width = cards_per_row * (card_width + padding) + padding
    section_height = rows * (card_height + padding) + padding

    # 绘制边框
    draw.rectangle([start_x, start_y, start_x + section_width, start_y + section_height], outline="black", width=3)

    title_y = start_y - 80
    line_y = title_y + 60

    # 绘制六边形
    hex_y = line_y + 2
    hex_width = 80
    hex_height = 60
    bias = hex_height * 0.3
    points = [
        (start_x, hex_y - hex_height / 2),
        (start_x + bias, hex_y - hex_height),
        (start_x + hex_width - bias, hex_y - hex_height),
        (start_x + hex_width, line_y - hex_height / 2),
        (start_x + hex_width - bias, hex_y),
        (start_x + bias, hex_y)
    ]
    draw.polygon(points, fill="black")

    # 绘制svg
    icon_width = 32
    icon_height = 40
    png_data = cairosvg.svg2png(url=deck_icon_file, output_width=icon_width, output_height=icon_height)
    if png_data:
        deck_icon = Image.open(io.BytesIO(png_data))
        img.paste(deck_icon, (start_x + (hex_width - icon_width) // 2, hex_y - (hex_height + icon_height) // 2))

    # 绘制分割线
    line_start = (start_x + hex_width, line_y) 
    line_end = (start_x + section_width, line_y)
    draw.line([line_start, line_end], fill="black", width=3)
    
    # 绘制标题
    draw.text((start_x + hex_width + 20, title_y), title, fill="black", font=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 40))

    # 绘制卡片
    for i, card_img in enumerate(card_images):
        if card_img:
            x = (i % cards_per_row) * (card_width + padding) + start_x + padding
            y = (i // cards_per_row) * (card_height + padding) + start_y + padding
            img.paste(card_img, (x, y))


async def generate_deck_image(deck_text, id, match, result="", deck_name=""):
    main_deck, extra_deck, side_deck = parse_ydk(deck_text)

    if len(extra_deck) > 15 or len(side_deck) > 15:
        return None

    card_width, card_height = 200, 290
    padding = 5
    font_size = 100
    cards_per_row = 10
    margin = 30  # 图片外边距

    # 计算图片高度
    main_rows = (len(main_deck) + cards_per_row - 1) // cards_per_row
    extra_rows = 2
    side_rows = 2

    total_height = (
        (main_rows + extra_rows + side_rows) * (card_height + padding)
        + 4 * padding
        + 3 * font_size
        + 2 * margin
    )
    total_width = cards_per_row * (card_width + padding) + padding + 2 * margin

    # 创建空白图片
    img = Image.new("RGB", (total_width, total_height), color="white")
    draw = ImageDraw.Draw(img)

    # 批量获取卡片图片
    all_card_ids = main_deck + extra_deck + side_deck
    all_card_images = await batch_get_images(all_card_ids)

    # 分割批量获取的图片结果
    main_images = all_card_images[:len(main_deck)]
    extra_images = all_card_images[len(main_deck):len(main_deck) + len(extra_deck)]
    side_images = all_card_images[len(main_deck) + len(extra_deck):]

    # 绘制主卡组
    y_offset = margin + font_size
    draw_section(img, main_images, "主卡组", margin, y_offset, card_width, card_height, main_rows, padding)

    # 绘制额外卡组
    y_offset += main_rows * (card_height + padding) + 2 * padding + font_size
    draw_section(img, extra_images, "额外卡组", margin, y_offset, card_width, card_height, extra_rows, padding)

    # 绘制副卡组
    y_offset += extra_rows * (card_height + padding) + 2 * padding + font_size
    draw_section(img, side_images, "副卡组", margin, y_offset, card_width, card_height, side_rows, padding)

    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 60)
    bbox = font.getbbox("Generated by 神人都市")
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = total_width - margin - 2*padding - text_width
    text_y = total_height - margin - 2*padding - text_height
    draw.text((text_x, text_y), "Generated by 神人都市", fill=(192,192,192), font=font)

    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 50)
    bbox = font.getbbox("神人都市")
    text_height = bbox[3] - bbox[1]
    text_x = total_width / 2 + 50
    text_y = y_offset - font_size - 50 - text_height
    draw.text((text_x, text_y), deck_name, fill="black", font=font)
    
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 50)
    text_y -= text_height + margin // 2 
    draw.text((text_x, text_y), f"{match} {result}", fill="black", font=font)

    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 80)
    bbox = font.getbbox("神人都市")
    text_height = bbox[3] - bbox[1]
    text_y -= text_height + margin
    draw.text((text_x, text_y), id, fill="black", font=font)

    return img



async def generate_card_list_image(id_list):
    card_width, card_height = 200, 290
    cards_per_row = 15
    padding = 5
    margin = 30
    all_card_images = await batch_get_images(id_list, card_width, card_height)
    n = len(all_card_images)
    rows = (n + cards_per_row - 1) // cards_per_row
    total_width = cards_per_row * (card_width + padding) + padding + 2 * margin
    total_height = rows * (card_height + padding) + padding + 2 * margin

    img = Image.new("RGB", (total_width, total_height), color="white")
    for i, card_img in enumerate(all_card_images):
        if card_img:
            x = (i % cards_per_row) * (card_width + padding) + margin + padding
            y = (i // cards_per_row) * (card_height + padding) + margin + padding
            img.paste(card_img, (x, y))
    return img



def generate_subset_font(original_font_path, output_font_path, text):
    characters = ''.join(sorted(set(c for c in text)))

    command = [
        "pyftsubset",
        original_font_path,
        f"--output-file={output_font_path}",
        f"--text={characters}",
    ]

    subprocess.run(command)

async def generate_deck_list_pdf(deck_text, language="sc", file_name=None):
    main_deck, extra_deck, side_deck = parse_ydk(deck_text)

    if len(extra_deck) > 15 or len(side_deck) > 15:
        return None

    doc = fitz.open(decklist_template)
    page = doc[0]

    cards = []
    # main deck
    card_count = OrderedDict()
    for card in main_deck:
        if card in card_count:
            card_count[card] += 1
        else:
            card_count[card] = 1
    
    t = [0] * 3
    for card_id, count in card_count.items():
        card_info = await get_card_info_by_id(card_id)
        type = 0
        card_name = ""
        font_name = language
        if card_info:
            type_text = card_info["text"]["types"]
            if "怪兽" in type_text:
                type = 0
            elif "魔法" in type_text:
                type = 1
            elif "陷阱" in type_text:
                type = 2
            card_name = card_info.get(language + "_name")
            if not card_name:
                card_name = card_info.get("jp_name", "")
                font_name = "jp"
            if font_name != "jp":
                font_name = "sc"
        else:
            return None
        
        cards.append({"count": str(count), "name": card_name, "pos": [0, type, t[type]], "font": font_name})
        t[type] += 1

    # extra deck
    card_count = OrderedDict()
    for card in extra_deck:
        if card in card_count:
            card_count[card] += 1
        else:
            card_count[card] = 1
    
    t = 0
    for card_id, count in card_count.items():
        card_info = await get_card_info_by_id(card_id)
        card_name = ""
        font_name = language
        if card_info:
            card_name = card_info.get(language + "_name")
            if not card_name:
                card_name = card_info.get("jp_name", "")
                font_name = "jp"
            if font_name != "jp":
                font_name = "sc"
        else:
            return None
        
        cards.append({"count": str(count), "name": card_name, "pos": [1, 0, t], "font": font_name})
        t += 1

    # side deck
    card_count = OrderedDict()
    for card in side_deck:
        if card in card_count:
            card_count[card] += 1
        else:
            card_count[card] = 1
    
    t = 0
    for card_id, count in card_count.items():
        card_info = await get_card_info_by_id(card_id)
        card_name = ""
        font_name = language
        if card_info:
            card_name = card_info.get(language + "_name")
            if not card_name:
                card_name = card_info.get("jp_name", "")
                font_name = "jp"
            if font_name != "jp":
                font_name = "sc"
        else:
            return None
        
        cards.append({"count": str(count), "name": card_name, "pos": [1, 1, t], "font": font_name})
        t += 1

    sc_text = ""
    jp_text = ""
    for card in cards:
        if card["font"] == "jp":
            jp_text += card["name"]
        else:
            sc_text += card["name"]
        sc_text += card["count"]

    
    fonts = {}
    #if len(sc_text):
    generate_subset_font(font_path_sc, font_path_sc_subset, sc_text)
    page.insert_font(fontfile=font_path_sc_subset, fontname="sc")
    fonts["sc"] = fitz.Font(fontfile=font_path_sc_subset)
    #if len(jp_text):
    generate_subset_font(font_path_jp, font_path_jp_subset, jp_text)
    page.insert_font(fontfile=font_path_jp_subset, fontname="jp")
    fonts["jp"] = fitz.Font(fontfile=font_path_jp_subset)

    tabx = 5
    x1 = 191
    x2 = 252
    x3 = 617
    dx = x3-x1
    taby = 3.5
    y = 502.5
    ny = 887.5
    dy = 39.4
    for card in cards:
        X = dx*card["pos"][1]
        Y = ny*card["pos"][0]+dy*card["pos"][2]
        name_size = 20
        width = fonts[card["font"]].text_length(card["name"], 20)
        if width > x3-x2-2*tabx:
            name_size = 20*(x3-x2-2*tabx)/width
        TY = (dy-(dy-taby*2)*name_size/20)/2
        
        rect = fitz.Rect(x1+X, taby+y+Y, x2+X, y+Y+dy)
        page.insert_textbox(rect, card["count"], fontname="sc", fontsize=20, align=fitz.TEXT_ALIGN_CENTER) # type: ignore
        
        rect = fitz.Rect(tabx+x2+X, TY+y+Y, x3+X, y+Y+dy)
        page.insert_textbox(rect, card["name"], fontname=card["font"], fontsize=name_size) # type: ignore

    if not file_name:
        pdf_buffer = io.BytesIO()
        doc.save(pdf_buffer, deflate=True)
        doc.close()
        pdf_buffer.seek(0)
        return pdf_buffer
        # return rasterize_pdf_to_image_pdf(pdf_buffer)
    
    output_path = os.path.join(PDF_DIR, file_name)
    doc.save(output_path, deflate=True)
    doc.close()
    # rasterize_pdf_to_image_pdf(output_path, output_path)
    return output_path

def record_deck_usage(info):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} {info}\n")