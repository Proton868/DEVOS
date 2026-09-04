"""Account plan/profile/onboarding — identity not UCIP authority."""
from core.account_constants import PUBLIC_PLANS, ROLES
from brain.personas import get_persona


def test_public_plans_exclude_elder_hegemon():
    assert "elder" not in PUBLIC_PLANS
    assert "hegemon" not in PUBLIC_PLANS
    assert "recruit" in PUBLIC_PLANS
    assert "conclave" in PUBLIC_PLANS
    assert "hegemon" in ROLES


def test_plan_is_not_persona_authority():
    assert get_persona("writer") is not None
    assert "deployment.production" not in (get_persona("writer").capabilities or [])


def test_frontend_auth_boot_not_token_trust():
    src = open("frontend-src/src/store/useStore.js").read()
    assert "isAuthenticated: false" in src
    assert "resolved only after verifySession" in src


def test_app_onboarding_phases_present():
    src = open("frontend-src/src/App.jsx").read()
    assert "SectPlanSurface" in src
    assert "ProfileSetupSurface" in src
    assert "SpatialTour" in src
    assert "onboardingPhase" in src
