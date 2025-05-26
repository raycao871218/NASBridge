#!/usr/bin/env python3

import json
import sys
import os
import requests
from typing import List, Tuple, Dict

GITHUB_HOSTS_URL = "https://raw.hellogithub.com/hosts.json"
# Thanks to the source for the GitHub hosts data:
# https://github.com/521xueweihan/GitHub520

def fetch_hosts_data(url: str) -> List[List[str]]:
    """
    Fetch hosts data from the specified URL.
    """
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from {url}: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON from {url}: {e}")
        sys.exit(1)

def parse_hosts_entries(json_data: List[List[str]]) -> List[Tuple[str, str]]:
    """
    Parse the JSON data into a list of (ip, hostname) tuples.
    Keep original order from API: [ip, hostname]
    """
    return [(entry[0], entry[1]) for entry in json_data]

def format_hosts_entries(entries: List[Tuple[str, str]]) -> str:
    """
    Format the entries into hosts file format.
    """
    # Add a header comment
    output = "# GitHub domains - Auto-generated\n"
    
    # Format each entry (ip is first in tuple, hostname is second)
    formatted_entries = [f"{ip}\t{hostname}" for ip, hostname in entries]
    return output + "\n".join(formatted_entries) + "\n"

def parse_current_github_entries(content: str) -> Dict[str, str]:
    """
    Parse existing GitHub entries from hosts file content.
    Returns a dictionary of hostname -> IP
    """
    entries = {}
    in_github_section = False
    for line in content.splitlines():
        if line.strip() == "# GitHub domains - Auto-generated":
            in_github_section = True
            continue
        if in_github_section:
            if line.startswith("#") and line != "# GitHub domains - Auto-generated":
                break
            if line.strip():
                parts = line.strip().split()
                if len(parts) >= 2:
                    ip, hostname = parts[0], parts[1]  # Order is IP -> hostname
                    entries[hostname] = ip
    return entries

def check_for_updates(current_entries: Dict[str, str], new_entries: List[Tuple[str, str]]) -> Tuple[List[Tuple[str, str]], bool]:
    """
    Compare current and new entries to determine which ones need updating.
    Returns a tuple of (updated_entries, has_changes)
    """
    updates_needed = []
    has_changes = False
    new_entries_dict = {hostname: ip for ip, hostname in new_entries}  # Split into hostname->IP mapping

    # Check for changes in existing entries
    for hostname, new_ip in new_entries_dict.items():
        current_ip = current_entries.get(hostname)
        if current_ip != new_ip:
            has_changes = True
            print(f"Update needed for {hostname}: {current_ip} -> {new_ip}")
        updates_needed.append((new_ip, hostname))  # Keep (IP, hostname) order

    # Check for new entries
    for hostname in new_entries_dict:
        if hostname not in current_entries:
            has_changes = True
            print(f"New entry: {hostname} -> {new_entries_dict[hostname]}")

    # Check for removed entries
    for hostname in current_entries:
        if hostname not in new_entries_dict:
            has_changes = True
            print(f"Entry removed: {hostname}")

    return updates_needed, has_changes

def update_hosts_file(new_content: str, hosts_path: str) -> bool:
    """
    Update the GitHub section in the hosts file.
    Preserves all other content in the hosts file.
    Returns True if any changes were made.
    """
    try:
        # Read the current content if file exists
        current_content = ""
        if os.path.exists(hosts_path):
            with open(hosts_path, 'r') as f:
                current_content = f.read()

        # Parse current GitHub entries
        current_entries = parse_current_github_entries(current_content)

        # Parse new entries from the formatted content
        new_entries = []
        for line in new_content.splitlines():
            if not line.startswith('#') and line.strip():
                parts = line.strip().split()
                if len(parts) >= 2:
                    new_entries.append((parts[0], parts[1]))

        # Check for updates
        updated_entries, has_changes = check_for_updates(current_entries, new_entries)

        if not has_changes:
            print("No updates needed - all entries are current")
            return False

        # Format updated entries
        formatted_content = "# GitHub domains - Auto-generated\n"
        formatted_content += "\n".join(f"{ip}\t{hostname}" for ip, hostname in updated_entries)
        formatted_content += "\n"

        # Find and remove the old GitHub section if it exists
        start_marker = "# GitHub domains - Auto-generated"
        sections = current_content.split(start_marker)
        
        if len(sections) > 1:
            # Find the end of the GitHub section (next section start or EOF)
            github_end = sections[1].find('\n#')
            if github_end == -1:  # No next section found
                base_content = sections[0]
            else:
                # Keep the rest of the file after GitHub section
                base_content = sections[0] + sections[1][github_end:]
        else:
            base_content = current_content

        # Remove any trailing newlines and add exactly one
        base_content = base_content.rstrip() + '\n\n'
        
        # Write the updated content
        with open(hosts_path, 'w') as f:
            f.write(base_content + formatted_content)

        return True

    except Exception as e:
        print(f"Failed to update hosts file: {e}")
        sys.exit(1)

def main():
    # Fetch JSON data from the URL
    json_data = fetch_hosts_data(GITHUB_HOSTS_URL)

    # Process the entries
    entries = parse_hosts_entries(json_data)
    formatted_content = format_hosts_entries(entries)

    # Default hosts file path (can be overridden by command line argument)
    hosts_path = "/etc/hosts"
    if len(sys.argv) > 1:
        hosts_path = sys.argv[1]

    # Update the hosts file and check if changes were made
    if update_hosts_file(formatted_content, hosts_path):
        print(f"Successfully updated {hosts_path} with changes")
    else:
        print(f"No changes needed in {hosts_path}")

if __name__ == "__main__":
    main()