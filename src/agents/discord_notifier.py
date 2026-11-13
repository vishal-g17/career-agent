from typing import Dict, Any, List
from ..engine.discord_client import DiscordClient
from .. import config

class DiscordNotifier:
    def __init__(self):
        self.client = DiscordClient()
        self.posts_channel_id = config.DISCORD_POSTS_CHANNEL_ID
        self.jobs_channel_id = config.DISCORD_JOBS_CHANNEL_ID

    def send_post_review(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends newly drafted post to dedicated LinkedIn Posts Discord channel.
        """
        content_preview = post_data["cleaned_draft"]
        hook_name = post_data.get("hook_name", "Product Insight")
        human_score = post_data["human_score_after"]["overall"]
        verdict = post_data["human_score_after"]["verdict"]
        char_count = post_data.get("char_count", len(content_preview))
        post_id = post_data["post_id"]

        embed = {
            "title": f"📝 Daily Post Draft: {hook_name}",
            "description": f"```markdown\n{content_preview}\n```",
            "color": 0x0A66C2,  # LinkedIn brand blue
            "fields": [
                {"name": "🛡️ Human Score", "value": f"**{human_score} / 100** ({verdict})", "inline": True},
                {"name": "📏 Length", "value": f"{char_count} chars", "inline": True},
                {"name": "⏰ Scheduled Window", "value": "8:45 AM IST Peak", "inline": True},
                {
                    "name": "⚡ Instructions",
                    "value": "Reply in thread:\n• `/post approve` - Lock and publish\n• `/post edit <notes>` - Rewrite with your feedback\n• `/post reroll` - Generate fresh angle",
                    "inline": False
                }
            ],
            "footer": {"text": f"ID: {post_id} | LinkedIn 24/7 Agent"}
        }

        # Send root notification message to POSTS channel
        msg = self.client.send_message(
            channel_id=self.posts_channel_id,
            content=f"🔔 **New LinkedIn Post Draft Ready** | Post ID: `{post_id}`",
            embeds=[embed]
        )

        message_id = msg.get("id")
        thread_id = None
        if message_id:
            try:
                # Create a thread for discussion and approval
                thread_res = self.client.create_thread(
                    channel_id=self.posts_channel_id,
                    message_id=message_id,
                    name=f"Post Review: {hook_name[:40]}"
                )
                thread_id = thread_res.get("id")
            except Exception as e:
                print(f"[DiscordNotifier] Thread creation notice: {e}")

        return {"message_id": message_id, "thread_id": thread_id}

    def send_job_alert(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends hot fresh job match to dedicated JOBS Discord channel with platform branding.
        """
        title = job_data.get("title", "Product Role")
        company = job_data.get("company", "Tech Company")
        loc = job_data.get("location", "India")
        posted = job_data.get("posted_time", "<24h ago")
        url = job_data.get("url", "https://linkedin.com/jobs")
        score = job_data.get("match_score", 75)
        reasons = job_data.get("reasons", [])
        platform = job_data.get("platform", "LinkedIn")

        # Color coding: Indeed Blue (0x2164f4), Naukri Cyan (0x00A5EC), LinkedIn Green/Blue (0x0A66C2)
        color = 0x2164F4 if platform == "Indeed" else (0x00A5EC if platform == "Naukri" else 0x2ECC71)

        embed = {
            "title": f"🔥 [{platform}] [{score}/100 Match] {title} @ {company}",
            "url": url,
            "color": color,
            "fields": [
                {"name": "🌐 Platform", "value": f"**{platform}**", "inline": True},
                {"name": "📍 Location", "value": loc, "inline": True},
                {"name": "⏰ Freshness", "value": posted, "inline": True},
                {"name": "🎯 Fit Score", "value": f"**{score}/100**", "inline": True},
                {
                    "name": "💡 Why You Match (From Your CV)",
                    "value": "\n".join(f"✅ {r}" for r in reasons) if reasons else "Matches candidate product & analyst criteria.",
                    "inline": False
                }
            ],
            "footer": {"text": f"Click title link to apply directly on {platform}"}
        }

        return self.client.send_message(
            channel_id=self.jobs_channel_id,
            content=f"🚨 **Fresh Product Job Alert** (<24h from {platform}) for <@{config.DISCORD_USER_ID}>:",
            embeds=[embed]
        )

    def send_feed_lead_alert(self, lead_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends an informal hiring lead discovered directly on user's personal LinkedIn feed to JOBS channel.
        """
        author = lead_data.get("author", "LinkedIn Connection")
        role = lead_data.get("role_matched", "Product Role")
        snippet = lead_data.get("snippet", "")
        emails = lead_data.get("emails", [])
        
        email_field_value = ", ".join(f"`{e}`" for e in emails) if emails else "None detected in post text"
        recommended_action = (
            f"✉️ **Direct Email Found:** {email_field_value}\nReply with `/email send {emails[0]}` to dispatch cold pitch!"
            if emails else
            "Send a direct message (DM) or connect with the author on LinkedIn."
        )

        embed = {
            "title": f"📢 [Feed Lead] Hiring: {role.upper()}",
            "description": f"**Author/Connection:** {author}\n\n```text\n{snippet[:400]}...\n```",
            "color": 0x9B59B6,  # Purple for network leads
            "fields": [
                {"name": "📍 Source", "value": "Your Personal LinkedIn Feed", "inline": True},
                {"name": "🎯 Role Detected", "value": role, "inline": True},
                {"name": "✉️ Email Leads", "value": email_field_value, "inline": False},
                {"name": "⚡ Recommended Action", "value": recommended_action, "inline": False}
            ],
            "footer": {"text": "Discovered via Ridealong Personal Feed Scanner"}
        }

        return self.client.send_message(
            channel_id=self.jobs_channel_id,
            content=f"👀 **Informal Network Hiring Post Found on Your Feed** for <@{config.DISCORD_USER_ID}>:",
            embeds=[embed]
        )

    def send_application_alert(self, app_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends confirmed job application alert (external site, Google form, LinkedIn Easy Apply, Naukri, Indeed)
        to the dedicated #agent channel.
        """
        company = app_data.get("company", "Company")
        role = app_data.get("role", "Product / Analyst Role")
        platform = app_data.get("platform", "External Portal")
        url = app_data.get("url", "")
        status = app_data.get("status", "SUBMITTED & CONFIRMED")
        details = app_data.get("details", "")
        resume_used = app_data.get("resume", "Vishal_Gupta_Resume.pdf (LaTeX)")

        # Colors: Green for success
        color = 0x2ECC71

        fields = [
            {"name": "🏢 Company", "value": f"**{company}**", "inline": True},
            {"name": "💼 Role", "value": f"**{role}**", "inline": True},
            {"name": "🌐 Platform", "value": f"**{platform}**", "inline": True},
            {"name": "📄 Resume Attached", "value": f"`{resume_used}`", "inline": True},
            {"name": "📊 Status", "value": f"✅ **{status}**", "inline": True},
        ]
        if url:
            fields.append({"name": "🔗 Application Link / Portal", "value": url, "inline": False})
        if details:
            fields.append({"name": "📝 Details / Screening Answers", "value": f"```text\n{details[:600]}\n```", "inline": False})

        embed = {
            "title": f"🎯 [Application Submitted] {role} @ {company}",
            "url": url if url else None,
            "color": color,
            "fields": fields,
            "footer": {"text": f"Antigravity Autonomous Multi-Agent | {platform}"}
        }

        # Send to agent channel (config.DISCORD_AGENT_CHANNEL_ID)
        agent_channel = getattr(config, "DISCORD_AGENT_CHANNEL_ID", "1569246471251959838")
        return self.client.send_message(
            channel_id=agent_channel,
            content=f"🚀 **Job Application Successfully Submitted** | **{company}** ({role})",
            embeds=[embed]
        )
