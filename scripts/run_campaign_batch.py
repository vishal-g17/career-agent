import sys
import os
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.outreach_campaign import send_outreach_batch, get_campaign_stats
from src.engine.discord_client import DiscordClient
from src import config

def main():
    client = DiscordClient()
    target_channel = config.DISCORD_JOBS_CHANNEL_ID
    
    batch_size = 500
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        batch_size = int(sys.argv[1])

    client.send_message(
        channel_id=target_channel,
        content=(
            f"🚀 **Maximum Blitz Cold Outreach Campaign Initiated!**\n"
            f"• **Target Batch Size**: **{batch_size} Emails** (As Many As Possible)\n"
            f"• **Dual-Sender Rotation**: Alternating between NSUT Workspace (`@nsut.ac.in`) & Personal Gmail (`@gmail.com`)\n"
            f"• **Cadence**: 25–45s natural human pauses (100% primary inbox deliverability)\n"
            f"• **Anti-Bounce Shield**: Live DNS MX verification active\n"
            f"• **Real-Time Reply Detector**: Active across both inboxes"
        )
    )

    print(f"[CampaignBatch] Starting maximum blitz of {batch_size} emails across NSUT & Gmail...")
    res = send_outreach_batch(batch_size=batch_size, sender_mode="rotate")
    
    stats = get_campaign_stats()
    breakdown_lines = []
    for sender, count in stats.get("sender_breakdown", {}).items():
        s_label = "NSUT Workspace" if "nsut" in str(sender).lower() else "Personal Gmail"
        breakdown_lines.append(f"  - `{sender}` ({s_label}): **{count}** sent")
    breakdown_str = "\n".join(breakdown_lines) if breakdown_lines else "None recorded"

    completion_msg = (
        f"📊 **Cold Outreach Blitz Batch Completed!**\n"
        f"• Attempted: **{res['attempted']}**\n"
        f"• Successfully Delivered: **{res['sent']}**\n"
        f"• Bounced / Filtered: **{res['bounced']}**\n"
        f"• Remaining Pending Leads: **{stats['pending']}**\n"
        f"• Total Contacted: **{stats['sent_total']}**\n"
        f"**Sender Distribution:**\n{breakdown_str}\n"
        f"\n*Follow-ups automatically scheduled for 3 days from now if no reply.*"
    )
    client.send_message(channel_id=target_channel, content=completion_msg)
    print(f"[CampaignBatch] Blitz Finished. Delivered: {res['sent']}, Bounced: {res['bounced']}")

if __name__ == "__main__":
    main()
