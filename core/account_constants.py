"""Account plan/role constants — identity labels, not UCIP authority."""
PUBLIC_PLANS = ("recruit", "outer_sect", "inner_sect", "conclave")
ALL_PLANS = PUBLIC_PLANS + ("hegemon",)
ROLES = ("member", "elder", "hegemon")
ONBOARDING_STATES = (
    "NOT_STARTED",
    "PLAN_SELECTED",
    "PROFILE_PENDING",
    "TOUR_PENDING",
    "COMPLETED",
    "SKIPPED",
)
