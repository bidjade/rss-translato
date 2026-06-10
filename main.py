import os
import feedparser
import time
import logging
import re
import requests
import configparser
import json
from urllib.parse import urlparse
from datetime import datetime
import xml.etree.ElementTree as ET
from xml.dom import minidom

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
RSS_DIR = os.path.join(BASE_DIR, "rss")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(RSS_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")
FEEDS_FILE = os.path.join(CONFIG_DIR, "feeds.txt")
TRACKER_FILE = os.path.join(CONFIG_DIR, "last_post.json")

config = configparser.ConfigParser()
config.read(CONFIG_FILE, encoding='utf-8')

GEMINI_API_KEY = config.get('credentials', 'gemini_api_key', fallback=None)

models_raw = config.get('models', 'gemini_models', fallback='')
if ',' in models_raw:
    GEMINI_MODELS = [model.strip() for model in models_raw.split(',') if model.strip()]
elif '\n' in models_raw:
    GEMINI_MODELS = [model.strip() for model in models_raw.split('\n') if model.strip() and not model.strip().startswith('[')]
else:
    GEMINI_MODELS = [models_raw.strip()] if models_raw.strip() else []

if not GEMINI_MODELS:
    logging.error("No models found in config.ini under [models] section")
    exit(1)

logging.info(f"Loaded {len(GEMINI_MODELS)} models from config: {GEMINI_MODELS}")

LANGUAGE = config.get('settings', 'language', fallback='arabic')
CONTENT_TYPE = config.get('settings', 'type', fallback='summary')

logging.info(f"Language: {LANGUAGE}")
logging.info(f"Content type: {CONTENT_TYPE}")

RSS_FEEDS = []
if os.path.exists(FEEDS_FILE):
    with open(FEEDS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            feed_url = line.strip()
            if feed_url and not feed_url.startswith('#') and feed_url.startswith('http'):
                RSS_FEEDS.append(feed_url)
else:
    logging.error(f"Feeds file not found: {FEEDS_FILE}")
    with open(FEEDS_FILE, 'w', encoding='utf-8') as f:
        f.write("# RSS Feeds List\n")
        f.write("https://feed.alternativeto.net/news/all\n")
    RSS_FEEDS = ["https://feed.alternativeto.net/news/all"]
    logging.info("Created default feeds.txt file")

if not RSS_FEEDS:
    logging.error("No RSS feeds configured. Please add feeds to config/feeds.txt")
    exit(1)

logging.info(f"Loaded {len(RSS_FEEDS)} RSS feeds")

USER_AGENT_HEADER = {'User-Agent': 'Mozilla/5.0'}

def load_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error reading tracker file: {e}")
    return {}

def save_tracker(tracker_data):
    try:
        with open(TRACKER_FILE, 'w', encoding='utf-8') as f:
            json.dump(tracker_data, f, indent=2, ensure_ascii=False)
        logging.info("Tracker saved successfully")
    except Exception as e:
        logging.error(f"Failed to write tracker file: {e}")

def normalize_url(url):
    if not url or not isinstance(url, str):
        return ""
    
    url = url.strip()
    while url.endswith('//'):
        url = url[:-1]
    if url.endswith('/'):
        url = url[:-1]
    
    return url

def extract_feed_name(feed_url, feed_data=None):
    try:
        if feed_data and hasattr(feed_data, 'feed'):
            if hasattr(feed_data.feed, 'title') and feed_data.feed.title:
                name = feed_data.feed.title
                name = re.sub(r'[^\w\s-]', '', name)
                name = name.strip().replace(' ', '_').lower()
                if name and len(name) < 50:
                    return name
        
        parsed = urlparse(feed_url)
        domain = parsed.netloc.replace('www.', '')
        domain_parts = domain.split('.')
        
        if len(domain_parts) >= 2:
            name = domain_parts[-2] if domain_parts[-2] not in ['com', 'org', 'net', 'io', 'co'] else domain_parts[-3] if len(domain_parts) >= 3 else domain_parts[0]
        else:
            name = domain_parts[0]
        
        name = re.sub(r'[^\w\s-]', '', name)
        name = name.strip().lower()
        
        return name
    except Exception as e:
        logging.warning(f"Failed to extract feed name: {e}")
        return f"feed_{abs(hash(feed_url)) % 10000}"

def create_rss_xml(feed_name, entries):
    rss = ET.Element('rss')
    rss.set('version', '2.0')
    rss.set('xmlns:content', 'http://purl.org/rss/1.0/modules/content/')
    
    channel = ET.SubElement(rss, 'channel')
    
    ET.SubElement(channel, 'title').text = f"{feed_name} - Processed Feed"
    ET.SubElement(channel, 'link').text = f"https://github.com/your-repo/apps-bot"
    ET.SubElement(channel, 'description').text = f"Processed RSS feed from {feed_name}"
    ET.SubElement(channel, 'language').text = LANGUAGE
    ET.SubElement(channel, 'lastBuildDate').text = datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT')
    
    for entry in entries:
        item = ET.SubElement(channel, 'item')
        
        title = entry.get('title', 'No Title')
        ET.SubElement(item, 'title').text = title
        
        link = entry.get('link', '')
        ET.SubElement(item, 'link').text = link
        
        guid = entry.get('guid') or entry.get('id') or link
        ET.SubElement(item, 'guid').text = guid
        
        pub_date = entry.get('published', datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT'))
        ET.SubElement(item, 'pubDate').text = pub_date
        
        description = entry.get('processed_text', entry.get('summary', ''))
        ET.SubElement(item, 'description').text = description
        
        if 'original_text' in entry:
            ET.SubElement(item, 'content:encoded').text = entry['original_text']
        
        if 'image_url' in entry and entry['image_url']:
            ET.SubElement(item, 'enclosure', {
                'url': entry['image_url'],
                'type': 'image/jpeg'
            })
    
    xml_str = ET.tostring(rss, encoding='unicode')
    dom = minidom.parseString(xml_str)
    pretty_xml = dom.toprettyxml(indent='  ', encoding='utf-8')
    
    xml_file = os.path.join(RSS_DIR, f"{feed_name}.xml")
    with open(xml_file, 'wb') as f:
        f.write(pretty_xml)
    
    logging.info(f"Created RSS XML file: {xml_file}")
    return xml_file

class GeminiModelSwitcher:
    def __init__(self, models):
        self.models = models
        self.current_index = 0
        self.all_models_failed = False
    
    def get_current_model(self):
        return self.models[self.current_index]
    
    def get_next_model(self):
        if self.current_index < len(self.models) - 1:
            self.current_index += 1
            return self.models[self.current_index]
        self.all_models_failed = True
        return None
    
    def reset(self):
        self.current_index = 0
        self.all_models_failed = False

def clean_html(raw_html):
    return re.sub(r'<[^>]+>', '', raw_html).strip()

def process_with_gemini(text, model_switcher):
    if not GEMINI_API_KEY:
        logging.error("GEMINI_API_KEY is not set.")
        return text[:500]
    
    if CONTENT_TYPE == 'translate':
        prompt = f"""Translate the following text to {LANGUAGE}. Translate it completely and accurately.

IMPORTANT RULES:
1. Translate the FULL text without summarizing or shortening
2. Do NOT add any hashtags
3. Return ONLY the translation without any additional comments or notes
4. Preserve the original meaning accurately
5. If translating to Arabic, make sure the translation is natural and fluent

Original text:
{text}"""
    else:
        prompt = f"""Summarize the following text in one paragraph in {LANGUAGE}. Keep it around 70 words (between 65-75 words).

IMPORTANT RULES:
1. Write the summary in {LANGUAGE} language
2. Do NOT add any hashtags
3. Return ONLY the summary text without any additional comments or notes
4. Keep the total text under 500 characters
5. If summarizing in Arabic, start with an Arabic word, not an English word or company name

Example for Arabic summary:
Correct: "أعلنت شركة جوجل اليوم عن تحديث جديد..."
Wrong: "Google أعلنت اليوم عن تحديث..."

Original text:
{text}"""
    
    start_index = model_switcher.current_index
    attempted_models = 0
    
    while attempted_models < len(model_switcher.models):
        current_model = model_switcher.get_current_model()
        attempted_models += 1
        
        logging.info(f"Attempt {attempted_models}/{len(model_switcher.models)}: Using model: {current_model}")
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={GEMINI_API_KEY}"
        
        max_tokens = 500 if CONTENT_TYPE == 'translate' else 200
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "topP": 0.8,
                "maxOutputTokens": max_tokens
            }
        }
        headers = {"Content-Type": "application/json"}
        
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            
            result = r.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            
            result = re.sub(r'#\w+\s*', '', result).strip()
            
            if CONTENT_TYPE == 'summary' and LANGUAGE == 'arabic':
                first_word = result.split()[0] if result.split() else ""
                if first_word and not re.match(r'^[\u0600-\u06FF]', first_word):
                    logging.warning(f"Text starts with non-Arabic word. Trying next model...")
                    next_model = model_switcher.get_next_model()
                    if next_model:
                        continue
            
            char_count = len(result)
            logging.info(f"Processing successful with model: {current_model}")
            logging.info(f"Characters: {char_count}")
            
            if char_count > 500:
                logging.warning(f"Text exceeds 500 chars ({char_count}). Truncating...")
                result = result[:497] + "..."
            
            return result
        
        except Exception as e:
            logging.error(f"Gemini API failed with model {current_model}: {e}")
            next_model = model_switcher.get_next_model()
            if next_model:
                continue
            else:
                break
    
    model_switcher.current_index = start_index
    logging.error("Failed to process with all Gemini models")
    return text[:500]

def process_feed(feed_url, model_switcher):
    try:
        logging.info(f"{'='*60}")
        logging.info(f"Processing feed: {feed_url}")
        
        feed = feedparser.parse(feed_url)
        
        if not feed.entries:
            logging.warning(f"No entries found in {feed_url}")
            return
        
        feed_name = extract_feed_name(feed_url, feed)
        logging.info(f"Feed name: {feed_name}")
        
        tracker_data = load_tracker()
        last_id = tracker_data.get(feed_name, "")
        logging.info(f"Last processed ID for {feed_name}: '{last_id}'")
        
        entries_sorted = sorted(feed.entries, 
                               key=lambda e: e.get('published_parsed') or e.get('updated_parsed') or (0,))
        
        processed_entries = []
        new_entries_to_process = []
        
        if not last_id:
            logging.info(f"First time processing '{feed_name}'. Processing latest post only.")
            new_entries_to_process = [entries_sorted[-1]]
        else:
            last_index = -1
            for i, entry in enumerate(entries_sorted):
                current_id = normalize_url(entry.get('guid') or entry.get('id') or entry.get('link'))
                if current_id == last_id:
                    last_index = i
                    break
            
            if last_index >= 0:
                new_entries_to_process = entries_sorted[last_index + 1:]
                logging.info(f"Found {len(new_entries_to_process)} new posts in {feed_name}")
            else:
                logging.warning(f"Last ID not found. Processing latest post only.")
                new_entries_to_process = [entries_sorted[-1]]
        
        if new_entries_to_process:
            for entry in new_entries_to_process:
                try:
                    post_id = normalize_url(entry.get('guid') or entry.get('id') or entry.get('link'))
                    post_url = entry.get('link', '')
                    
                    logging.info(f"Processing post: {post_id}")
                    
                    desc = entry.get('summary', '') or entry.get('description', '')
                    desc_text = clean_html(desc)
                    
                    processed_text = process_with_gemini(desc_text, model_switcher)
                    
                    processed_entry = {
                        'title': entry.get('title', 'No Title'),
                        'link': post_url,
                        'guid': post_id,
                        'published': entry.get('published', ''),
                        'summary': processed_text,
                        'original_text': desc_text,
                        'processed_text': processed_text,
                        'image_url': entry.get('media_content', [{}])[0].get('url', '') if 'media_content' in entry else None
                    }
                    
                    processed_entries.append(processed_entry)
                    
                    tracker_data[feed_name] = post_id
                    
                    logging.info(f"Successfully processed post: {entry.get('title', 'No Title')[:50]}...")
                    
                except Exception as e:
                    logging.error(f"Failed to process individual post: {e}")
                    continue
        
        if processed_entries:
            create_rss_xml(feed_name, processed_entries)
            save_tracker(tracker_data)
        else:
            logging.info(f"No new posts to save for {feed_name}")
        
    except Exception as e:
        logging.error(f"Failed to process feed {feed_url}: {e}")

def main():
    logging.info("Starting Apps Bot...")
    logging.info(f"Loaded {len(GEMINI_MODELS)} Gemini models")
    logging.info(f"Processing {len(RSS_FEEDS)} RSS feeds")
    logging.info(f"Language: {LANGUAGE}")
    logging.info(f"Content type: {CONTENT_TYPE}")
    
    if not GEMINI_API_KEY:
        logging.error("GEMINI_API_KEY is not configured. Please add it to config/config.ini")
        return
    
    model_switcher = GeminiModelSwitcher(GEMINI_MODELS)
    
    for feed_url in RSS_FEEDS:
        process_feed(feed_url, model_switcher)
        time.sleep(2)
    
    logging.info(f"{'='*60}")
    logging.info("All feeds processed successfully!")
    logging.info(f"RSS files saved in: {RSS_DIR}")
    logging.info(f"Tracker file: {TRACKER_FILE}")

if __name__ == "__main__":
    main()
