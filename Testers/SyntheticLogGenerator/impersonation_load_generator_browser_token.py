"""
Impersonation Load Generator - Multi-Tenant / Sandbox Version
=============================================================

Runs two sequential tenant phases:
  Phase 1 — Tenant 1  (en-US / USD / Indigo)
  Phase 2 — Tenant 2  (ko-KR / KRW / Teal – ACME)

Each phase:
  1. Prompts for the tenant admin's Bearer token (copy from browser DevTools).
  2. Fetches /api/users to discover the tenant's user pool.
  3. Spawns CONCURRENT_USERS async workers that create nominations until
     TARGET_NOMINATIONS_PER_TENANT successful creates are recorded.
  4. Optionally auto-approves every created nomination via the beneficiary's
     manager.
  5. Prints per-tenant statistics, then moves to the next phase.

A combined summary is printed at the end.

Usage
-----
  python impersonation_load_generator_browser_token.py

Environment variable overrides
--------------------------------
  API_BASE_URL   Override the API base URL (default: http://localhost:8000)
"""

import asyncio
import aiohttp
import random
import os
from datetime import datetime
from typing import List, Dict, Optional
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================


API_BASE_URL = "https://award-nomination-api-sandbox-f5cnf7aze0d6e0h7.z02.azurefd.net" #API_BASE_URL", "http://localhost:8000")

# Nominations to create per tenant phase
TARGET_NOMINATIONS_PER_TENANT = 1000

# Async workers per phase (keep low enough to avoid overwhelming localhost)
CONCURRENT_USERS = 20

# Delay range between actions per worker (seconds)
ACTION_DELAY_MIN = 0.1
ACTION_DELAY_MAX = 0.5

# ── Tenant phase definitions ─────────────────────────────────────────────────

TENANT_PHASES = [
    {
        "name": "Tenant 1",
        "label": "en-US / USD / Indigo",
        "admin_hint": "david64.terian@terian-services.com",
        # USD amounts (integers matching the Amount field)
        "amounts": [50, 100, 150, 200, 250, 300],
        "descriptions_normal": [
            "Outstanding performance on the Q4 project delivery",
            "Exceptional teamwork and collaboration with the team",
            "Innovative solution that saved significant time and resources",
            "Going above and beyond to help customers succeed",
            "Mentoring and developing junior team members",
            "Successfully leading a critical project milestone to completion",
        ],
        "descriptions_suspicious": ["Good work", "Nice job", "Great effort"],
        "descriptions_fraudulent": ["x", ".", "good", "ok"],
    },
    {
        "name": "Tenant 2",
        "label": "ko-KR / KRW / Teal (ACME)",
        "admin_hint": "(ACME tenant admin account)",
        # KRW amounts — numerically larger than USD
        "amounts": [50000, 100000, 150000, 200000, 250000, 300000],
        "descriptions_normal": [
            "4분기 프로젝트를 탁월하게 완수하였습니다",
            "팀 협업 및 협력에서 뛰어난 성과를 보여주었습니다",
            "시간과 자원을 크게 절감하는 혁신적인 해결책을 제시하였습니다",
            "고객 성공을 위해 기대 이상의 노력을 기울였습니다",
            "신입 팀원들의 멘토링과 역량 개발에 힘써주었습니다",
            "중요한 프로젝트 마일스톤을 성공적으로 이끌었습니다",
        ],
        "descriptions_suspicious": ["수고했어요", "잘했어요", "최고예요"],
        "descriptions_fraudulent": ["x", ".", "좋아요", "ok"],
    },
]


# ============================================================================
# TOKEN HELPER
# ============================================================================

def get_token_from_user(tenant_name: str, admin_hint: str) -> str:
    """
    Prompt the user to paste a Bearer token obtained from the browser.

    Instructions:
      1. Open {API_BASE_URL}/docs (Swagger UI) — or the running frontend.
      2. Click "Authorize" and log in as the tenant admin.
      3. Open DevTools (F12) → Network tab.
      4. Trigger any API call (e.g., GET /api/users).
      5. Copy the value after "Bearer " in the Authorization header.
    """
    print()
    print("=" * 70)
    print(f"  TOKEN REQUIRED — {tenant_name} ({admin_hint})")
    print("=" * 70)
    print(f"\n  1. Open: {API_BASE_URL}/docs")
    print(f"  2. Log in as: {admin_hint}")
    print(f"  3. DevTools (F12) → Network → any request → Authorization header")
    print(f"  4. Copy everything after 'Bearer '")
    print()

    token = input("  🔑 Paste token here: ").strip()

    if token.startswith("Bearer "):
        token = token[7:]

    if not token:
        raise ValueError("Token is required — cannot proceed without authentication.")

    logger.info(f"✅ Token accepted for {tenant_name} (length: {len(token)} chars)")
    return token


# ============================================================================
# PER-TENANT LOAD GENERATOR
# ============================================================================

class TenantLoadGenerator:
    """
    Runs a count-based load test for a single tenant.
    Workers stop as soon as TARGET_NOMINATIONS_PER_TENANT are recorded.
    """

    def __init__(
        self,
        admin_token: str,
        phase_config: Dict,
        target: int = TARGET_NOMINATIONS_PER_TENANT,
        concurrent_users: int = CONCURRENT_USERS,
        auto_approve: bool = True,
    ):
        self.admin_token = admin_token
        self.phase = phase_config
        self.target = target
        self.concurrent_users = concurrent_users
        self.auto_approve = auto_approve
        self.api_base = API_BASE_URL

        # User pool — populated by fetch_users()
        self.users: List[Dict] = []
        self.users_by_id: Dict[int, Dict] = {}
        self.eligible_nominators: List[Dict] = []
        self.eligible_beneficiaries: List[Dict] = []

        self.stats = {
            "nominations_created": 0,
            "nominations_approved": 0,
            "nominations_failed": 0,
            "approvals_failed": 0,
            "fraud_blocked": 0,
        }

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _auth_headers(self, impersonate_upn: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.admin_token}",
            "Content-Type": "application/json",
        }
        if impersonate_upn:
            headers["X-Impersonate-User"] = impersonate_upn
        return headers

    def _target_reached(self) -> bool:
        return self.stats["nominations_created"] >= self.target

    # ── User discovery ────────────────────────────────────────────────────────

    async def fetch_users(self, session: aiohttp.ClientSession) -> None:
        logger.info(f"[{self.phase['name']}] Fetching users from /api/users ...")

        async with session.get(
            f"{self.api_base}/api/users",
            headers=self._auth_headers(),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(
                    f"[{self.phase['name']}] Failed to fetch users: "
                    f"{resp.status} — {text}"
                )
            self.users = await resp.json()

        self.users_by_id = {u["UserId"]: u for u in self.users}

        for user in self.users:
            self.eligible_nominators.append(user)
            if user.get("ManagerId"):
                self.eligible_beneficiaries.append(user)

        logger.info(
            f"[{self.phase['name']}] Loaded {len(self.users)} users — "
            f"{len(self.eligible_nominators)} nominators, "
            f"{len(self.eligible_beneficiaries)} beneficiaries"
        )

        if not self.eligible_beneficiaries:
            raise RuntimeError(
                f"[{self.phase['name']}] No eligible beneficiaries "
                "(need users with ManagerId set)."
            )

    # ── Nomination creation ───────────────────────────────────────────────────

    async def create_nomination(
        self,
        session: aiohttp.ClientSession,
        nominator_upn: str,
        beneficiary_id: int,
        amount: int,
        description: str,
    ) -> Optional[Dict]:
        """Create one nomination. Returns the response dict or None on failure."""
        # Pre-flight: bail out if target already reached
        if self._target_reached():
            return None

        payload = {
            "BeneficiaryId": beneficiary_id,
            "Amount": amount,
            "NominationDescription": description,
        }

        try:
            async with session.post(
                f"{self.api_base}/api/nominations",
                json=payload,
                headers=self._auth_headers(impersonate_upn=nominator_upn),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                status = resp.status
                text = await resp.text()

                if status == 201:
                    self.stats["nominations_created"] += 1
                    try:
                        result = await resp.json() if text else {}
                    except Exception:
                        result = {"status": "created"}
                    nom_id = result.get("NominationId", "?")
                    logger.info(
                        f"[{self.phase['name']}] ✅ #{self.stats['nominations_created']:04d} "
                        f"NomId={nom_id} | {nominator_upn} → BeneficiaryId={beneficiary_id} "
                        f"| Amount={amount}"
                    )
                    return result

                elif status == 400 and "fraud" in text.lower():
                    self.stats["fraud_blocked"] += 1
                    logger.warning(
                        f"[{self.phase['name']}] 🚫 Fraud blocked: "
                        f"{nominator_upn} → {beneficiary_id} | Amount={amount}"
                    )
                    return None

                else:
                    self.stats["nominations_failed"] += 1
                    logger.error(
                        f"[{self.phase['name']}] ❌ Nomination failed ({status}): "
                        f"{nominator_upn} → {beneficiary_id} — {text[:120]}"
                    )
                    return None

        except Exception as exc:
            self.stats["nominations_failed"] += 1
            logger.error(
                f"[{self.phase['name']}] ❌ Nomination error ({nominator_upn}): {exc}"
            )
            return None

    # ── Approval ─────────────────────────────────────────────────────────────

    async def approve_nomination(
        self,
        session: aiohttp.ClientSession,
        nomination_id: int,
        manager_upn: str,
    ) -> bool:
        try:
            async with session.post(
                f"{self.api_base}/api/nominations/approve",
                json={"NominationId": nomination_id, "Approved": True},
                headers=self._auth_headers(impersonate_upn=manager_upn),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    self.stats["nominations_approved"] += 1
                    logger.info(
                        f"[{self.phase['name']}] ✅ Approved NomId={nomination_id} "
                        f"by {manager_upn}"
                    )
                    return True
                else:
                    self.stats["approvals_failed"] += 1
                    text = await resp.text()
                    logger.error(
                        f"[{self.phase['name']}] ❌ Approval failed ({resp.status}): "
                        f"NomId={nomination_id} — {text[:120]}"
                    )
                    return False
        except Exception as exc:
            self.stats["approvals_failed"] += 1
            logger.error(
                f"[{self.phase['name']}] ❌ Approval error NomId={nomination_id}: {exc}"
            )
            return False

    # ── Behaviour generators ─────────────────────────────────────────────────

    async def _normal(self, session: aiohttp.ClientSession) -> None:
        """70% — realistic single nomination, optionally auto-approved."""
        nominator = random.choice(self.eligible_nominators)
        beneficiary = random.choice(self.eligible_beneficiaries)

        if nominator["UserId"] == beneficiary["UserId"]:
            return

        amount = random.choice(self.phase["amounts"])
        description = random.choice(self.phase["descriptions_normal"])

        result = await self.create_nomination(
            session,
            nominator["userPrincipalName"],
            beneficiary["UserId"],
            amount,
            description,
        )

        if self.auto_approve and result and result.get("NominationId"):
            manager_id = beneficiary.get("ManagerId")
            if manager_id and manager_id in self.users_by_id:
                manager = self.users_by_id[manager_id]
                await asyncio.sleep(random.uniform(0.2, 0.8))
                await self.approve_nomination(
                    session, result["NominationId"], manager["userPrincipalName"]
                )

    async def _suspicious(self, session: aiohttp.ClientSession) -> None:
        """20% — burst of nominations from one nominator to a small pool."""
        nominator = random.choice(self.eligible_nominators)
        pool_size = min(3, len(self.eligible_beneficiaries))
        beneficiary_pool = random.sample(self.eligible_beneficiaries, pool_size)

        for _ in range(random.randint(3, 5)):
            if self._target_reached():
                break
            beneficiary = random.choice(beneficiary_pool)
            if nominator["UserId"] == beneficiary["UserId"]:
                continue
            await self.create_nomination(
                session,
                nominator["userPrincipalName"],
                beneficiary["UserId"],
                random.choice(self.phase["amounts"][:2]),   # low-end amounts
                random.choice(self.phase["descriptions_suspicious"]),
            )
            await asyncio.sleep(random.uniform(0.05, 0.2))

    async def _fraudulent(self, session: aiohttp.ClientSession) -> None:
        """10% — rapid repeated nominations from one person to one target."""
        nominator = random.choice(self.eligible_nominators)
        beneficiary = random.choice(self.eligible_beneficiaries)

        if nominator["UserId"] == beneficiary["UserId"]:
            return

        for _ in range(random.randint(8, 12)):
            if self._target_reached():
                break
            await self.create_nomination(
                session,
                nominator["userPrincipalName"],
                beneficiary["UserId"],
                random.choice(self.phase["amounts"][:2]),
                random.choice(self.phase["descriptions_fraudulent"]),
            )
            await asyncio.sleep(random.uniform(0.03, 0.1))

    # ── Worker ───────────────────────────────────────────────────────────────

    async def _worker(self, session: aiohttp.ClientSession, worker_id: int) -> None:
        """One virtual user — keeps firing actions until target is reached."""
        action_count = 0
        logger.info(f"[{self.phase['name']}] ▶ Worker {worker_id:02d} started")

        while not self._target_reached():
            scenario = random.random()
            try:
                if scenario < 0.70:
                    await self._normal(session)
                elif scenario < 0.90:
                    await self._suspicious(session)
                else:
                    await self._fraudulent(session)
                action_count += 1
            except Exception as exc:
                logger.error(
                    f"[{self.phase['name']}] ❌ Worker {worker_id} error: {exc}"
                )

            await asyncio.sleep(random.uniform(ACTION_DELAY_MIN, ACTION_DELAY_MAX))

        logger.info(
            f"[{self.phase['name']}] ✔ Worker {worker_id:02d} done — "
            f"{action_count} actions"
        )

    # ── Runner ────────────────────────────────────────────────────────────────

    async def run(self) -> Dict:
        """Execute the full phase and return stats."""
        start = datetime.now()
        logger.info("")
        logger.info("=" * 70)
        logger.info(
            f"  PHASE: {self.phase['name']}  ({self.phase['label']})"
        )
        logger.info(
            f"  Target: {self.target} nominations | "
            f"Workers: {self.concurrent_users} | "
            f"Auto-approve: {self.auto_approve}"
        )
        logger.info(f"  API: {self.api_base}")
        logger.info("=" * 70)

        connector = aiohttp.TCPConnector(limit=self.concurrent_users + 5)
        async with aiohttp.ClientSession(connector=connector) as session:
            await self.fetch_users(session)

            logger.info(
                f"\n▶ Launching {self.concurrent_users} workers "
                f"(target: {self.target} nominations) ...\n"
            )

            workers = [
                self._worker(session, wid)
                for wid in range(1, self.concurrent_users + 1)
            ]
            await asyncio.gather(*workers)

        elapsed = (datetime.now() - start).total_seconds()
        self.stats["elapsed_seconds"] = round(elapsed, 1)

        logger.info("")
        logger.info(f"  {self.phase['name']} complete in {elapsed:.1f}s")
        logger.info(f"  Nominations created:  {self.stats['nominations_created']:5d}")
        logger.info(f"  Nominations approved: {self.stats['nominations_approved']:5d}")
        logger.info(f"  Fraud blocked:        {self.stats['fraud_blocked']:5d}")
        logger.info(f"  Create failures:      {self.stats['nominations_failed']:5d}")
        logger.info(f"  Approval failures:    {self.stats['approvals_failed']:5d}")

        return self.stats


# ============================================================================
# APPROVE-ALL HELPER  (option 2 from the main menu)
# ============================================================================

async def approve_all_pending(admin_token: str, tenant_label: str) -> int:
    """
    Impersonate every manager in the tenant and approve their pending
    nominations. Returns the number approved.
    """
    logger.info(f"\n[{tenant_label}] Fetching all users to identify managers ...")

    # Re-use a minimal generator instance just for its helper methods
    phase_cfg = {"name": tenant_label, "amounts": [], "descriptions_normal": [],
                 "descriptions_suspicious": [], "descriptions_fraudulent": []}
    gen = TenantLoadGenerator(
        admin_token=admin_token,
        phase_config=phase_cfg,
        target=0,
        concurrent_users=1,
        auto_approve=False,
    )

    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
        await gen.fetch_users(session)

        managers = [
            u for u in gen.users
            if any(x.get("ManagerId") == u["UserId"] for x in gen.users)
        ]
        logger.info(f"[{tenant_label}] Found {len(managers)} managers")

        all_pending: List[Dict] = []
        for mgr in managers:
            try:
                async with session.get(
                    f"{gen.api_base}/api/nominations/pending",
                    headers=gen._auth_headers(impersonate_upn=mgr["userPrincipalName"]),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        batch = await resp.json()
                        for nom in batch:
                            nom["_manager_upn"] = mgr["userPrincipalName"]
                        all_pending.extend(batch)
            except Exception as exc:
                logger.warning(
                    f"[{tenant_label}] Could not fetch pending for "
                    f"{mgr['userPrincipalName']}: {exc}"
                )

        logger.info(
            f"[{tenant_label}] Approving {len(all_pending)} pending nominations ..."
        )
        approved = 0
        for nom in all_pending:
            ok = await gen.approve_nomination(
                session, nom["NominationId"], nom["_manager_upn"]
            )
            if ok:
                approved += 1
            await asyncio.sleep(0.05)

    return approved


# ============================================================================
# MAIN — sequential multi-tenant load test
# ============================================================================

async def run_multitenant_load_test(auto_approve: bool) -> None:
    """Run all TENANT_PHASES sequentially, collecting stats for each."""
    all_stats = []

    for phase in TENANT_PHASES:
        print()
        print("=" * 70)
        print(f"  NEXT PHASE: {phase['name']}  ({phase['label']})")
        print("=" * 70)

        token = get_token_from_user(phase["name"], phase["admin_hint"])

        gen = TenantLoadGenerator(
            admin_token=token,
            phase_config=phase,
            target=TARGET_NOMINATIONS_PER_TENANT,
            concurrent_users=CONCURRENT_USERS,
            auto_approve=auto_approve,
        )

        stats = await gen.run()
        all_stats.append({"phase": phase["name"], "label": phase["label"], **stats})

    # ── Aggregate summary ─────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("  MULTI-TENANT LOAD TEST — AGGREGATE SUMMARY")
    logger.info("=" * 70)
    totals = {k: 0 for k in ("nominations_created", "nominations_approved",
                               "nominations_failed", "approvals_failed",
                               "fraud_blocked")}
    for s in all_stats:
        logger.info(
            f"\n  {s['phase']} ({s['label']})  — {s.get('elapsed_seconds', 0):.1f}s"
        )
        for k in totals:
            logger.info(f"    {k:<26s}: {s.get(k, 0):5d}")
            totals[k] += s.get(k, 0)

    logger.info("")
    logger.info("  TOTALS ACROSS ALL TENANTS")
    for k, v in totals.items():
        logger.info(f"    {k:<26s}: {v:5d}")
    logger.info("=" * 70)


async def run_approve_pending() -> None:
    """Approve all pending nominations — for each tenant separately."""
    for phase in TENANT_PHASES:
        print()
        print("=" * 70)
        print(f"  APPROVE PENDING — {phase['name']}  ({phase['label']})")
        print("=" * 70)
        token = get_token_from_user(phase["name"], phase["admin_hint"])
        approved = await approve_all_pending(token, phase["name"])
        logger.info(f"[{phase['name']}] ✅ Approved {approved} nominations")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    print()
    print("=" * 70)
    print("  AWARD NOMINATION — MULTI-TENANT LOAD TESTING TOOL")
    print(f"  Target API : {API_BASE_URL}")
    print(f"  Per tenant : {TARGET_NOMINATIONS_PER_TENANT} nominations")
    print(f"  Workers    : {CONCURRENT_USERS} concurrent")
    print("=" * 70)
    print()
    print("  1. Run load test (Tenant 1 → Tenant 2, 1 000 nominations each)")
    print("  2. Approve all pending nominations (both tenants)")
    print("  3. Exit")
    print()

    choice = input("  Enter choice (1-3): ").strip()

    if choice == "1":
        approve_input = input(
            "  Auto-approve nominations? [Y/n]: "
        ).strip().lower()
        auto_approve = approve_input != "n"
        exit_code = asyncio.run(run_multitenant_load_test(auto_approve))

    elif choice == "2":
        asyncio.run(run_approve_pending())
        exit_code = 0

    elif choice == "3":
        logger.info("Exiting.")
        exit_code = 0

    else:
        logger.error("Invalid choice. Exiting.")
        exit_code = 1

    exit(exit_code)
