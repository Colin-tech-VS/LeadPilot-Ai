import logging

logger = logging.getLogger(__name__)


def notify_high_urgency_lead(lead, tenant):
    """Simulate urgent lead notification (SMS/email integration later)."""
    message = (
        f"[ALERT] High urgency lead detected — "
        f"tenant={tenant.name} lead={lead.id} "
        f"phone={lead.phone} issue={lead.issue_type}"
    )
    print(message)
    logger.warning(message)
