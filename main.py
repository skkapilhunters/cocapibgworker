import json
import time
from datetime import datetime
import redis
import requests

# --- CONFIGURATION ---
# Replace with your actual hosted Redis connection URL (e.g., redis://username:password@host:port)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# Replace with your CoC Developer API Token
COC_API_TOKEN = os.environ.get("COC_KEYS", "")
COC_API_BASE = "https://api.clashofclans.com/v1"

# Initialize Redis client using the connection URL
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

def get_clans_from_redis():
    """Fetches the clan list array from the 'clans' key in Redis."""
    try:
        # Handles standard string or RedisJSON storage types
        raw_data = redis_client.get("clans")
        if not raw_data:
            try:
                raw_data = redis_client.json().get("clans")
            except Exception:
                pass
        
        if isinstance(raw_data, str):
            return json.loads(raw_data)
        elif isinstance(raw_data, list):
            return raw_data
        return []
    except Exception as e:
        print(f"Error fetching clans from Redis: {e}")
        return []

def fetch_and_store_clan_war(clan_entry, api_token):
    """Fetches current war for a specific clan and stores it under the 'war' folder namespace[cite: 2, 3]."""
    clan_tag = clan_entry.get("clantag")
    war_id = clan_entry.get("warid")
    
    if not clan_tag or not war_id:
        return

    if not clan_tag.startswith('#'):
        clan_tag = f"#{clan_tag}"
    
    encoded_tag = clan_tag.replace('#', '%23')
    url = f"{COC_API_BASE}/clans/{encoded_tag}/currentwar"
    
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"API Error for {clan_tag} [{response.status_code}]: {response.text}")
            return
        
        war_data = response.json()
        if war_data.get("state") == "notInWar":
            print(f"Clan {clan_tag} (War ID: {war_id}) is currently not in war.")
            return

        # Helper to format CoC API timestamps
        def format_coc_time(coc_time_str):
            if not coc_time_str:
                return ""
            try:
                dt = datetime.strptime(coc_time_str.split('.')[0], "%Y%m%dT%H%M%S")
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return coc_time_str

        state_mapping = {
            "preparation": "Preparation Day",
            "inWar": "Battle Day",
            "warEnded": "War Ended"
        }
        current_status = state_mapping.get(war_data.get("state"), war_data.get("state"))

        my_clan = war_data.get("clan", {})
        opponent_clan = war_data.get("opponent", {})

        # Build Rosters
        my_members = {m.get('mapPosition'): m for m in my_clan.get('members', [])}
        opp_members = {m.get('mapPosition'): m for m in opponent_clan.get('members', [])}
        all_positions = sorted(list(set(list(my_members.keys()) + list(opp_members.keys()))))
        rosters = []

        for pos in all_positions:
            clan_a_mem = my_members.get(pos, {})
            clan_b_mem = opp_members.get(pos, {})
            
            def parse_attacks(member_obj):
                attacks_list = []
                for idx, att in enumerate(member_obj.get('attacks', []), 1):
                    stars = att.get('stared', att.get('stars', 0))
                    destruction = att.get('destructionPercentage', 0)
                    attacks_list.append(f"Hit #{att.get('order', idx)}: {stars} stars {destruction}%")
                return attacks_list

            rosters.append({
                "position": pos,
                "clan_a_member": {
                    "tag": clan_a_mem.get('tag', ''),
                    "name": clan_a_mem.get('name', ''),
                    "attacks": parse_attacks(clan_a_mem),
                    "defenses": []
                },
                "clan_b_member": {
                    "tag": clan_b_mem.get('tag', ''),
                    "name": clan_b_mem.get('name', ''),
                    "attacks": parse_attacks(clan_b_mem),
                    "defenses": []
                }
            })

        # Construct final schema payload mapping your exact template structure
        payload = {
            "id": str(war_id),
            "doc_id": str(war_id),
            "clan_tag": clan_tag,
            "guild_id": clan_entry.get("guild_id"),
            "last_status": current_status,
            "migrated_at": datetime.utcnow().isoformat() + "+00:00",
            "conflict_data": {
                "war_metadata": {
                    "status": current_status,
                    "prep_day_start": format_coc_time(war_data.get("preparationStartTime")),
                    "battle_day_start": format_coc_time(war_data.get("startTime")),
                    "war_ends": format_coc_time(war_data.get("endTime")),
                    "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                },
                "clans": {
                    "clan_a": {
                        "tag": my_clan.get("tag"),
                        "name": my_clan.get("name"),
                        "level": my_clan.get("clanLevel"),
                        "badge": my_clan.get("badgeUrls", {}).get("medium", ""),
                        "members_count": my_clan.get("teamSize"),
                        "stars": my_clan.get("stars", 0),
                        "attacks_used": my_clan.get("attacks", 0),
                        "destruction_percentage": f"{my_clan.get('destructionPercentage', 0)}%",
                        "type": clan_entry.get("association", "FWA")
                    },
                    "clan_b": {
                        "tag": opponent_clan.get("tag"),
                        "name": opponent_clan.get("name"),
                        "level": opponent_clan.get("clanLevel"),
                        "badge": opponent_clan.get("badgeUrls", {}).get("medium", ""),
                        "members_count": opponent_clan.get("teamSize"),
                        "stars": opponent_clan.get("stars", 0),
                        "attacks_used": opponent_clan.get("attacks", 0),
                        "destruction_percentage": f"{opponent_clan.get('destructionPercentage', 0)}%",
                        "type": clan_entry.get("permanent association", "Official FWA")
                    }
                },
                "rosters": rosters
            }
        }

        # Save inside the 'war' folder namespace (e.g., war:1, war:2)[cite: 2]
        redis_key = f"war:{war_id}"
        redis_client.set(redis_key, json.dumps(payload))
        print(f"Saved: Redis key '{redis_key}' for clan {clan_tag}[cite: 2, 3]")

    except Exception as e:
        print(f"Error processing clan {clan_tag}: {e}")

def run_sync_task():
    """Main routine to batch and process clans."""
    print(f"\n[{datetime.now()}] Starting clan wars sync cycle...")
    clans = get_clans_from_redis()
    
    if not clans:
        print("No clans found in Redis 'clans' key[cite: 3].")
        return

    # Process clans in batches of 20 per request
    batch_size = 20
    for i in range(0, len(clans), batch_size):
        batch = clans[i:i + batch_size]
        print(f"-> Processing batch {i // batch_size + 1} ({len(batch)} clans)...")
        
        for clan_entry in batch:
            fetch_and_store_clan_war(clan_entry, COC_API_TOKEN)
        
        # Brief pause between batches to protect API rate limits
        time.sleep(2)

    print(f"[{datetime.now()}] Sync cycle finished.")

if __name__ == "__main__":
    print("Clan War Automation Service Started. Running every 5 minutes...")
    while True:
        run_sync_task()
        # Sleep for 5 minutes (300 seconds)
        time.sleep(300)
