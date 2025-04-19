import os
import re
import requests
from datetime import datetime
import difflib


def parse_bracket_info(folder_name):
    """
    Extracts expected release date and expected producer from the folder name’s bracketed segments.

    For each bracketed segment found:
      - If the segment consists of exactly 6 digits and represents a plausible YYMMDD date,
        it is assumed to be the release date.
      - Otherwise, if the segment contains alphanumeric characters with a minimum length of 3,
        it is assumed to be the producer.
      - Pure-digit segments longer than 6 digits are ignored.

    Returns a tuple: (expected_date, expected_producer).
    """
    matches = re.findall(r'\[(.*?)\]', folder_name)
    expected_date = ""
    expected_producer = ""
    for segment in matches:
        segment = segment.strip()
        if segment.isdigit() and len(segment) == 6:
            mm = int(segment[2:4])
            dd = int(segment[4:6])
            if 1 <= mm <= 12 and 1 <= dd <= 31:
                expected_date = segment
        elif len(segment) >= 3:
            if not (segment.isdigit() and len(segment) > 6):
                if not expected_producer:
                    expected_producer = segment
    return expected_date, expected_producer


def parse_folder_name(folder_name):
    """
    Extracts the main title from the folder name by removing any content in square brackets,
    parentheses, and curly braces.

    Returns a dictionary with the extracted title.
    """
    cleaned = re.sub(r'\[.*?\]', '', folder_name)
    cleaned = re.sub(r'\(.*?\)', '', cleaned)
    cleaned = re.sub(r'\{.*?\}', '', cleaned)
    cleaned = cleaned.strip()
    if "+" in cleaned:
        parts = cleaned.split("+", 1)
        title = parts[0].strip()
        return {"title": title, "release": ""}
    return {"title": cleaned, "release": ""}


def get_vn_candidates(search_query, expected_date="", expected_producer=""):
    """
    Searches the VNDB API for visual novel entries matching the search_query.

    Sends a POST request to /kana/vn with fields:
      id, title, released, developers.name, olang, length.
    For each candidate, computes a composite confidence score based on:
      - Title similarity,
      - Similarity between the expected release date and candidate’s release date,
      - Similarity between the expected producer and the candidate’s producer.
    If an expected value is missing, a neutral value (0.5) is used.

    Returns up to three tuples: (candidate_dict, composite_confidence).
    """
    url = "https://api.vndb.org/kana/vn"
    headers = {"Content-Type": "application/json"}
    payload = {
        "filters": ["search", "=", search_query],
        "fields": "id,title,released,developers.name,olang,length"
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
    except requests.HTTPError as http_err:
        print(f"HTTP error during VN search for '{search_query}': {response.status_code} {response.text}")
        return []
    except Exception as err:
        print(f"Error during VN search for '{search_query}': {err}")
        return []

    try:
        data = response.json()
    except Exception as err:
        print(f"Error parsing JSON for VN search '{search_query}': {err}")
        return []

    if "results" in data and isinstance(data["results"], list):
        candidates = data["results"]
    else:
        candidates = []

    if not candidates:
        return []

    candidates_with_conf = []
    for candidate in candidates:
        candidate_title = candidate.get("title", "")
        title_conf = difflib.SequenceMatcher(None, search_query.lower(), candidate_title.lower()).ratio()
        candidate_release = candidate.get("released", "")
        candidate_date = format_release_date(candidate_release) if candidate_release else ""
        if expected_date and candidate_date:
            date_conf = difflib.SequenceMatcher(None, expected_date, candidate_date).ratio()
        else:
            date_conf = 0.5
        devs = candidate.get("developers", [])
        if devs and isinstance(devs, list) and len(devs) > 0:
            candidate_dev = devs[0]
            if isinstance(candidate_dev, dict):
                candidate_producer = candidate_dev.get("name", "")
            else:
                candidate_producer = candidate_dev
        else:
            candidate_producer = ""
        if expected_producer and candidate_producer:
            prod_conf = difflib.SequenceMatcher(None, expected_producer.lower(), candidate_producer.lower()).ratio()
        else:
            prod_conf = 0.5
        composite_conf = (title_conf + date_conf + prod_conf) / 3
        candidates_with_conf.append((candidate, composite_conf))

    candidates_with_conf.sort(key=lambda x: x[1], reverse=True)
    return candidates_with_conf[:3]


def search_release(vn_id):
    """
    Retrieves release information for the given VN id from VNDB.

    Sends a POST request to /kana/release with fields:
      title, released, producers.name, producers.developer, official.
    The filter now uses ["id", "=", vn_id] to avoid HTTP 400 errors.
    Checks for both "result" and "results" keys.
    Returns the first official release if available; otherwise, the first release.
    """
    url = "https://api.vndb.org/kana/release"
    headers = {"Content-Type": "application/json"}
    payload = {
        "filters": ["id", "=", vn_id],
        "fields": "title,released,producers.name,producers.developer,official"
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
    except requests.HTTPError as http_err:
        print(f"HTTP error during release search for VN id '{vn_id}': {response.status_code} {response.text}")
        return None
    except Exception as err:
        print(f"Error during release search for VN id '{vn_id}': {err}")
        return None

    try:
        data = response.json()
    except Exception as err:
        print(f"Error parsing JSON for release search for VN id '{vn_id}': {err}")
        return None

    if isinstance(data, list):
        releases = data
    elif isinstance(data, dict):
        if "result" in data and isinstance(data["result"], list):
            releases = data["result"]
        elif "results" in data and isinstance(data["results"], list):
            releases = data["results"]
        elif "releases" in data and isinstance(data["releases"], list):
            releases = data["releases"]
        else:
            releases = []
    else:
        releases = []

    if not releases:
        print(f"No release found for VN id: {vn_id}")
        return None

    selected_release = None
    for release in releases:
        if release.get("official") is True:
            selected_release = release
            break
    if not selected_release:
        selected_release = releases[0]
    return selected_release


def search_tag(tag_query):
    """
    Searches the VNDB API for a tag matching tag_query using fields:
      id, name, aliases, description, category, vn_count.
    Returns the first matching tag or None.
    """
    url = "https://api.vndb.org/kana/tag"
    headers = {"Content-Type": "application/json"}
    payload = {
        "filters": ["search", "=", tag_query],
        "fields": "id,name,aliases,description,category,vn_count"
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
    except requests.HTTPError as http_err:
        print(f"HTTP error during tag search for '{tag_query}': {response.status_code} {response.text}")
        return None
    except Exception as err:
        print(f"Error during tag search for '{tag_query}': {err}")
        return None

    try:
        data = response.json()
    except Exception as err:
        print(f"Error parsing JSON for tag search '{tag_query}': {err}")
        return None

    if isinstance(data, list):
        tags = data
    elif isinstance(data, dict):
        if "result" in data and isinstance(data["result"], list):
            tags = data["result"]
        elif "results" in data and isinstance(data["results"], list):
            tags = data["results"]
        elif "tags" in data and isinstance(data["tags"], list):
            tags = data["tags"]
        else:
            tags = []
    else:
        tags = []

    if not tags:
        print(f"No tags found for: {tag_query}")
        return None
    return tags[0]


def format_release_date(release_date_str):
    """
    Formats a release date string (expected in YYYY-MM-DD) as YYMMDD.
    Returns "Unknown" if parsing fails.
    """
    try:
        dt = datetime.strptime(release_date_str, "%Y-%m-%d")
        return dt.strftime("%y%m%d")
    except Exception:
        return "Unknown"


def detect_ost(folder_path, folder_name, release_info=None):
    """
    Automatically detects if an OST is included by checking for keywords:
      "ost", "soundtrack", or "オリジナルサウンドトラック" (case-insensitive)
    in the folder name, the release title, and file names.

    This function now first checks whether the original folder name contains a curly-braced
    OST symbol (♫). If so, it returns True.
    """
    # Check for OST symbol inside curly braces in the folder name.
    if "♫" in folder_name:
        return True
    keywords = ["ost", "soundtrack", "オリジナルサウンドトラック"]
    if any(kw in folder_name.lower() for kw in keywords):
        return True
    if release_info and "title" in release_info:
        if any(kw in release_info.get("title", "").lower() for kw in keywords):
            return True
    try:
        for filename in os.listdir(folder_path):
            if any(kw in filename.lower() for kw in keywords):
                return True
    except Exception as e:
        print("Error accessing files in folder:", e)
    return False


def detect_fandisc_api(release_info, folder_name):
    """
    Checks if the release is indicated as a fandisc via API data or the folder name.
    Searches for keywords ("fandisc", "fan disc", "ファンディスク") (case-insensitive).
    """
    keywords = ["fandisc", "fan disc", "ファンディスク"]
    if release_info and "title" in release_info:
        if any(kw in release_info.get("title", "").lower() for kw in keywords):
            return True
    if any(kw in folder_name.lower() for kw in keywords):
        return True
    return False


def detect_fandisc_physical(folder_path):
    """
    Scans file names in the folder for keywords indicating fandisc content.
    Searches for "fandisc", "fan disc", or "ファンディスク" (case-insensitive).
    """
    keywords = ["fandisc", "fan disc", "ファンディスク"]
    try:
        for filename in os.listdir(folder_path):
            if any(kw in filename.lower() for kw in keywords):
                return True
    except Exception as e:
        print("Error accessing files in folder:", e)
    return False


def detect_language_simple(text):
    """
    A simple language detection function that checks for any Japanese characters.
    Returns "JP" if any Hiragana, Katakana, or CJK ideographs are found; otherwise, returns "EN".
    """
    for ch in text:
        code = ord(ch)
        if (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF) or (0x4E00 <= code <= 0x9FFF):
            return "JP"
    return "EN"


def suggest_new_folder_name(vn_info, release_info, optional_flags=None, optional_tags=None, custom_template=None):
    """
    Constructs the final folder name using the default template:
      [Producer][Release Date][L{Length}] Title {Optional Flags} (Optional Tags)
    (Language is omitted.)

    - Producer: derived from VN or release producers.
    - Release Date: formatted as YYMMDD from release or VN data.
    - [L{Length}]: VN's length (without a colon for Windows compatibility).
    - Title: VN's title.
    - Optional Flags and Tags: appended as provided.
    """
    # Determine Producer.
    producer = "Unknown Producer"
    producers = []
    if release_info:
        producers = release_info.get("producers", [])
    if producers:
        for prod in producers:
            if prod.get("developer") is True:
                producer = prod.get("name", "Unknown Producer")
                break
        else:
            producer = producers[0].get("name", "Unknown Producer")
    else:
        vn_devs = vn_info.get("developers", [])
        if vn_devs:
            producer = vn_devs[0].get("name", "Unknown Producer")

    # Determine Release Date.
    release_date_str = None
    if release_info:
        release_date_str = release_info.get("released")
    if not release_date_str:
        release_date_str = vn_info.get("released")
    formatted_date = format_release_date(release_date_str) if release_date_str else "Unknown"

    # Determine Length.
    length_val = vn_info.get("length")
    length_str = f"[L{length_val}]" if length_val is not None else ""

    # Determine Title.
    title = vn_info.get("title", "Unknown Title")

    flags = ""
    if optional_flags:
        flags = optional_flags.strip()

    if custom_template:
        folder_name = custom_template.format(
            producer=producer,
            release_date=formatted_date,
            length=length_str,
            title=title,
            flags=flags,
            tags=optional_tags if optional_tags else ""
        )
    else:
        folder_name = f"[{producer}][{formatted_date}]{length_str} {title}"
        if flags:
            folder_name += f" {{{flags}}}"
        if optional_tags:
            folder_name += f" ({optional_tags})"

    return folder_name


def main():
    base_dir = input("Enter the base directory for Visual Novel folders: ").strip()
    if not os.path.exists(base_dir):
        print("The directory does not exist!")
        return

    folders = [f for f in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, f))]
    if not folders:
        print("No folders found in the specified directory.")
        return

    print(f"\nFound {len(folders)} folder(s) in '{base_dir}'. Processing...\n")

    for folder in folders:
        print(f"\nFolder: {folder}")
        parsed = parse_folder_name(folder)
        extracted_title = parsed.get("title", folder)
        expected_date, expected_producer = parse_bracket_info(folder)
        print(f"Extracted title for API search: '{extracted_title}'")
        if expected_date:
            print(f"Expected release date from folder: '{expected_date}'")
        if expected_producer:
            print(f"Expected producer from folder: '{expected_producer}'")

        search_query = extracted_title
        print(f"Searching VNDB for visual novel: '{search_query}'")

        candidates = get_vn_candidates(search_query, expected_date, expected_producer)
        if not candidates and " " in search_query:
            new_query = search_query.split(" ", 1)[0].strip()
            print(f"No VN candidates found for '{search_query}'. Trying shortened search query: '{new_query}'")
            search_query = new_query
            candidates = get_vn_candidates(search_query, expected_date, expected_producer)

        while not candidates:
            new_term = input(
                f"No VN candidates found for '{search_query}'.\nExtracted title is '{extracted_title}'.\nEdit the search term: ") or extracted_title
            if not new_term.strip():
                print("No search term provided. Skipping renaming for this folder.\n")
                break
            search_query = new_term.strip()
            candidates = get_vn_candidates(search_query, expected_date, expected_producer)
        if not candidates:
            continue

        print("\nCandidates:")
        for idx, (candidate, confidence) in enumerate(candidates, start=1):
            cand_title = candidate.get("title", "Unknown Title")
            cand_release = candidate.get("released", "Unknown")
            cand_release_formatted = format_release_date(cand_release) if cand_release != "Unknown" else "Unknown"
            devs = candidate.get("developers", [])
            if devs and isinstance(devs, list) and len(devs) > 0:
                candidate_producer = devs[0].get("name", "Unknown") if isinstance(devs[0], dict) else devs[0]
            else:
                candidate_producer = "Unknown"
            print(
                f"{idx}: {cand_title} (Confidence: {confidence:.0%}) - Released: {cand_release_formatted}, Producer: {candidate_producer}")

        try:
            selection = int(input("Select a candidate by number (or 0 to skip renaming): ").strip())
        except ValueError:
            print("Invalid input. Skipping renaming for this folder.\n")
            continue

        if selection == 0 or selection > len(candidates):
            print("Skipping renaming for this folder.\n")
            continue

        vn_info = candidates[selection - 1][0]
        vn_id = vn_info.get("id")
        if vn_id and not str(vn_id).startswith("v"):
            vn_id = "v" + str(vn_id)
        release_info = search_release(vn_id) if vn_id else None
        if not vn_id:
            print("VN id not found; skipping release lookup.")

        user_flags = input(
            "Enter optional flags (e.g., 18+; separate with commas if multiple; or leave blank): ").strip()

        api_has_fandisc = detect_fandisc_api(release_info, folder)
        current_folder_path = os.path.join(base_dir, folder)
        physical_fandisc = detect_fandisc_physical(current_folder_path)

        fandisc_flag = ""
        if api_has_fandisc:
            if physical_fandisc:
                fandisc_flag = "★"
            else:
                fandisc_flag = "☆"
            print(f"Fandisc indicated by API; physical detection: {physical_fandisc}. Using flag: {fandisc_flag}")
        else:
            print("No fandisc indicated by API.")

        optional_flags = user_flags
        if fandisc_flag:
            if optional_flags:
                if fandisc_flag not in optional_flags:
                    optional_flags += f", {fandisc_flag}"
            else:
                optional_flags = fandisc_flag

        if detect_ost(current_folder_path, folder, release_info):
            if optional_flags:
                if "♫" not in optional_flags:
                    optional_flags += ", ♫"
            else:
                optional_flags = "♫"
            print("OST detected automatically; added OST flag (♫).")
        else:
            print("No OST detected automatically.")

        optional_tags = input(
            "Enter optional API tags (e.g., Romance, Slice of Life; comma-separated; or leave blank): ").strip()

        new_name = suggest_new_folder_name(vn_info, release_info, optional_flags=optional_flags,
                                           optional_tags=optional_tags)
        print(f"\nSuggested new folder name: {new_name}\n")

        choice = input("Rename folder? (y/n): ").strip().lower()
        if choice == 'y':
            old_path = os.path.join(base_dir, folder)
            new_path = os.path.join(base_dir, new_name)
            try:
                os.rename(old_path, new_path)
                print("Folder renamed successfully!\n")
            except Exception as e:
                print(f"Error renaming folder: {e}\n")
        else:
            print("Folder not renamed.\n")


if __name__ == "__main__":
    main()
