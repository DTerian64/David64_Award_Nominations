"""
Impersonation Load Generator - Browser Token Version
Uses a token obtained from Swagger UI (no ROPC needed!)
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
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "https://award-nomination-api-bqb8ftbdfpemfyck.z02.azurefd.net"
)


# ============================================================================
# TOKEN FROM BROWSER
# ============================================================================

def get_token_from_user() -> str:
    """
    Get admin token from user (obtained via browser/Swagger UI)
    
    Instructions for getting the token:
    1. Go to https://award-nomination-api-bqb8ftbdfpemfyck.z02.azurefd.net/docs
    2. Click the "Authorize" button (lock icon)
    3. Log in as david64.terian@terian-services.com
    4. Open browser DevTools (F12)
    5. Go to Application/Storage → Session Storage → your domain
    6. Find the token (or check Network tab for Authorization header)
    7. Copy the token and paste it here
    """
    print("="*70)
    print("GET ADMIN TOKEN FROM SWAGGER UI")
    print("="*70)
    print("\n📋 Instructions:")
    print(f"1. Open: {API_BASE_URL}/docs")
    print("2. Click 'Authorize' button (green lock icon)")
    print("3. Log in as david64.terian@terian-services.com")
    print("4. After login, you'll be redirected back")
    print("\n🔍 To get the token:")
    print("   Option A - Easy way:")
    print("     1. Try any endpoint (e.g., GET /api/users)")
    print("     2. Open browser DevTools (F12) → Network tab")
    print("     3. Find the request → Headers → Authorization")
    print("     4. Copy everything after 'Bearer '")
    print("\n   Option B - Direct from storage:")
    print("     1. Open DevTools (F12) → Application tab")
    print("     2. Session Storage → your domain")
    print("     3. Look for a key with 'token' in the name")
    print("     4. Copy the value")
    print("\n" + "="*70)
    
    token = input("\n🔑 Paste your token here: ").strip()
    
    # Remove "Bearer " if user included it
    if token.startswith("Bearer "):
        token = token[7:]
    
    if not token:
        raise ValueError("Token is required")
    
    logger.info(f"✅ Token received (length: {len(token)} chars)")
    return token


# ============================================================================
# IMPERSONATION-BASED LOAD GENERATOR
# ============================================================================

class ImpersonationLoadGenerator:
    """
    Load generator that uses admin impersonation to simulate users
    """
    
    def __init__(
        self,
        admin_token: str,
        concurrent_users: int = 50,
        duration_hours: float = 0.25,
        auto_approve: bool = True
    ):
        self.admin_token = admin_token
        self.concurrent_users = concurrent_users
        self.duration = duration_hours * 3600
        self.auto_approve = auto_approve
        self.api_base = API_BASE_URL
        
        # Will be populated from /api/users
        self.users: List[Dict] = []
        self.users_by_id: Dict[int, Dict] = {}
        self.eligible_nominators: List[Dict] = []
        self.eligible_beneficiaries: List[Dict] = []
        self.managers: List[Dict] = []
        
        # Statistics
        self.stats = {
            "nominations_created": 0,
            "nominations_approved": 0,
            "nominations_failed": 0,
            "approvals_failed": 0,
            "fraud_blocked": 0
        }
    
    def get_auth_headers(self, impersonate_upn: Optional[str] = None) -> Dict[str, str]:
        """Get authorization headers with optional impersonation"""
        headers = {
            "Authorization": f"Bearer {self.admin_token}",
            "Content-Type": "application/json"
        }
        
        if impersonate_upn:
            headers["X-Impersonate-User"] = impersonate_upn
        
        return headers
    
    async def fetch_users(self, session: aiohttp.ClientSession):
        """Fetch all users from the API"""
        logger.info("Fetching users from /api/users...")
        
        try:
            async with session.get(
                f"{self.api_base}/api/users",
                headers=self.get_auth_headers(),
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Failed to fetch users: {resp.status} - {text}")
                
                self.users = await resp.json()
                
                # Build lookup dictionary
                self.users_by_id = {user["UserId"]: user for user in self.users}
                
                # Categorize users
                for user in self.users:
                    self.eligible_nominators.append(user)
                    
                    if user.get("ManagerId"):
                        self.eligible_beneficiaries.append(user)
                    
                    if any(u.get("ManagerId") == user["UserId"] for u in self.users):
                        if user not in self.managers:
                            self.managers.append(user)
                
                logger.info(f"✅ Loaded {len(self.users)} users")
                logger.info(f"   - {len(self.eligible_nominators)} potential nominators")
                logger.info(f"   - {len(self.eligible_beneficiaries)} potential beneficiaries")
                logger.info(f"   - {len(self.managers)} managers")
                
                if not self.eligible_beneficiaries:
                    raise Exception("No eligible beneficiaries found (need users with ManagerId)")
                
        except Exception as e:
            logger.error(f"❌ Failed to fetch users: {e}")
            raise
    
    async def create_nomination(
        self,
        session: aiohttp.ClientSession,
        nominator_upn: str,
        beneficiary_id: int,
        dollar_amount: float,
        description: str
    ) -> Optional[Dict]:
        """Create a nomination by impersonating a nominator"""
        try:
            data = {
                "BeneficiaryId": beneficiary_id,
                "DollarAmount": dollar_amount,
                "NominationDescription": description
            }
            
            async with session.post(
                f"{self.api_base}/api/nominations",
                json=data,
                headers=self.get_auth_headers(impersonate_upn=nominator_upn),
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                status = resp.status
                text = await resp.text()
                
                if status == 201:
                    self.stats["nominations_created"] += 1
                    try:
                        result = await resp.json() if text else {}
                        nomination_id = result.get("NominationId")
                        logger.info(
                            f"✅ Nomination created: {nominator_upn} → "
                            f"Beneficiary {beneficiary_id}, ${dollar_amount} (ID: {nomination_id})"
                        )
                        return result
                    except:
                        logger.info(
                            f"✅ Nomination created: {nominator_upn} → "
                            f"Beneficiary {beneficiary_id}, ${dollar_amount}"
                        )
                        return {"status": "created"}
                
                elif status == 400 and "fraud" in text.lower():
                    self.stats["fraud_blocked"] += 1
                    logger.warning(
                        f"🚫 Nomination blocked (fraud): {nominator_upn} → "
                        f"Beneficiary {beneficiary_id}, ${dollar_amount}"
                    )
                    return None
                
                else:
                    self.stats["nominations_failed"] += 1
                    logger.error(
                        f"❌ Nomination failed ({status}): {nominator_upn} → "
                        f"Beneficiary {beneficiary_id}, ${dollar_amount} - {text[:100]}"
                    )
                    return None
        
        except Exception as e:
            self.stats["nominations_failed"] += 1
            logger.error(f"❌ Nomination error: {nominator_upn} - {e}")
            return None
    
    async def approve_nomination(
        self,
        session: aiohttp.ClientSession,
        nomination_id: int,
        manager_upn: str
    ) -> bool:
        """Approve a nomination by impersonating the manager"""
        try:
            async with session.post(
                f"{self.api_base}/api/nominations/{nomination_id}/approve",
                headers=self.get_auth_headers(impersonate_upn=manager_upn),
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                status = resp.status
                
                if status == 200:
                    self.stats["nominations_approved"] += 1
                    logger.info(f"✅ Nomination approved: ID {nomination_id} by {manager_upn}")
                    return True
                else:
                    self.stats["approvals_failed"] += 1
                    text = await resp.text()
                    logger.error(
                        f"❌ Approval failed ({status}): ID {nomination_id} by {manager_upn} - {text[:100]}"
                    )
                    return False
        
        except Exception as e:
            self.stats["approvals_failed"] += 1
            logger.error(f"❌ Approval error: ID {nomination_id} - {e}")
            return False
    
    async def generate_normal_behavior(self, session: aiohttp.ClientSession):
        """70% - Normal, realistic nominations"""
        nominator = random.choice(self.eligible_nominators)
        beneficiary = random.choice(self.eligible_beneficiaries)
        
        if nominator["UserId"] == beneficiary["UserId"]:
            return
        
        dollar_amount = random.choice([50, 100, 150, 200, 250, 300])
        description = random.choice([
            "Outstanding performance on the Q4 project delivery",
            "Exceptional teamwork and collaboration with the team",
            "Innovative solution that saved significant time and resources",
            "Going above and beyond to help customers succeed",
            "Mentoring and developing junior team members",
            "Successfully leading critical project milestone to completion"
        ])
        
        result = await self.create_nomination(
            session,
            nominator["userPrincipalName"],
            beneficiary["UserId"],
            dollar_amount,
            description
        )
        
        if self.auto_approve and result and result.get("NominationId"):
            manager_id = beneficiary.get("ManagerId")
            if manager_id and manager_id in self.users_by_id:
                manager = self.users_by_id[manager_id]
                await asyncio.sleep(random.uniform(0.5, 2.0))
                await self.approve_nomination(
                    session,
                    result["NominationId"],
                    manager["userPrincipalName"]
                )
    
    async def generate_suspicious_behavior(self, session: aiohttp.ClientSession):
        """20% - Suspicious patterns"""
        nominator = random.choice(self.eligible_nominators)
        pool_size = min(3, len(self.eligible_beneficiaries))
        beneficiary_pool = random.sample(self.eligible_beneficiaries, pool_size)
        
        for i in range(random.randint(3, 5)):
            beneficiary = random.choice(beneficiary_pool)
            if nominator["UserId"] == beneficiary["UserId"]:
                continue
            
            await self.create_nomination(
                session,
                nominator["userPrincipalName"],
                beneficiary["UserId"],
                random.choice([50, 100]),
                random.choice(["Good work", "Nice job", "Great effort"])
            )
            await asyncio.sleep(random.uniform(0.1, 0.3))
    
    async def generate_fraudulent_behavior(self, session: aiohttp.ClientSession):
        """10% - Fraudulent patterns"""
        nominator = random.choice(self.eligible_nominators)
        beneficiary = random.choice(self.eligible_beneficiaries)
        
        if nominator["UserId"] == beneficiary["UserId"]:
            return
        
        for i in range(random.randint(8, 12)):
            await self.create_nomination(
                session,
                nominator["userPrincipalName"],
                beneficiary["UserId"],
                random.choice([50, 100, 150]),
                random.choice(["x", ".", "good", "ok"])
            )
            await asyncio.sleep(random.uniform(0.05, 0.15))
    
    async def user_session(self, session: aiohttp.ClientSession, virtual_user_id: int):
        """Simulate one virtual user's session"""
        elapsed = 0
        action_count = 0
        
        logger.info(f"🚀 Starting session for virtual user {virtual_user_id}")
        
        while elapsed < self.duration:
            scenario = random.random()
            
            try:
                if scenario < 0.7:
                    await self.generate_normal_behavior(session)
                elif scenario < 0.9:
                    await self.generate_suspicious_behavior(session)
                else:
                    await self.generate_fraudulent_behavior(session)
                
                action_count += 1
            except Exception as e:
                logger.error(f"❌ Error for virtual user {virtual_user_id}: {e}")
            
            wait_time = random.uniform(2, 10)
            await asyncio.sleep(wait_time)
            elapsed += wait_time
        
        logger.info(
            f"✅ Session complete for virtual user {virtual_user_id}: "
            f"{action_count} actions in {elapsed:.1f}s"
        )
    
    async def run(self):
        """Run the load test"""
        logger.info("="*70)
        logger.info("Impersonation-Based Load Test Starting")
        logger.info(f"  Virtual users: {self.concurrent_users}")
        logger.info(f"  Duration: {self.duration/3600:.2f} hours")
        logger.info(f"  Auto-approve: {self.auto_approve}")
        logger.info(f"  API: {self.api_base}")
        logger.info("="*70)
        
        connector = aiohttp.TCPConnector(limit=100)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            await self.fetch_users(session)
            
            logger.info(f"\n▶️  Starting {self.concurrent_users} virtual user sessions...")
            
            tasks = [
                self.user_session(session, user_id)
                for user_id in range(1, self.concurrent_users + 1)
            ]
            
            await asyncio.gather(*tasks)
        
        logger.info("\n" + "="*70)
        logger.info("LOAD TEST COMPLETE - FINAL STATISTICS")
        logger.info("="*70)
        logger.info(f"Nominations Created:    {self.stats['nominations_created']:5d}")
        logger.info(f"Nominations Approved:   {self.stats['nominations_approved']:5d}")
        logger.info(f"Fraud Blocked:          {self.stats['fraud_blocked']:5d}")
        logger.info(f"Nomination Failures:    {self.stats['nominations_failed']:5d}")
        logger.info(f"Approval Failures:      {self.stats['approvals_failed']:5d}")
        logger.info("="*70)


# ============================================================================
# MAIN EXECUTION
# ============================================================================

async def main():
    """Main entry point"""
    
    try:
        # Get token from user (via browser/Swagger)
        admin_token = get_token_from_user()
        
        # Get test parameters
        print(f"\n📊 Load Test Configuration")
        concurrent_users = int(input(f"   Concurrent virtual users [50]: ") or "50")
        duration_minutes = float(input(f"   Duration in minutes [15]: ") or "15")
        auto_approve_input = input(f"   Auto-approve nominations? [Y/n]: ").lower()
        auto_approve = auto_approve_input != 'n'
        
        # Create and run load generator
        generator = ImpersonationLoadGenerator(
            admin_token=admin_token,
            concurrent_users=concurrent_users,
            duration_hours=duration_minutes / 60,
            auto_approve=auto_approve
        )
        
        await generator.run()
        return 0
        
    except KeyboardInterrupt:
        logger.info("\n⚠️  Load test interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
