from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError
from voteit.meeting.roles import ROLE_POTENTIAL_VOTER
from voteit.poll.abcs import ElectoralRegisterPolicy
from voteit.poll.abcs import VoteTransferPolicy
from voteit.poll.models import VoteTransfer
from voteit.poll.registries import er_policy
from voteit.poll.registries import vote_transfer_policies

if TYPE_CHECKING:
    from voteit.poll.models import Poll

__all__ = (
    "MainAndSubstVT",
    "MainSubstDelegatePolicy",
)

logger = getLogger(__name__)
User = get_user_model()

MAIN_ROLE_ID = "main"
SUBSTITUTE_ROLE_ID = "substitute"

if TYPE_CHECKING:
    from voteit.core.models import User


@vote_transfer_policies
class MainAndSubstVT(VoteTransferPolicy):
    """
    Allow transfer of vote power from potential voter with role main to subst_role
    """

    name = "main_and_subst"

    def check(self, source: User, target: User, modifying: VoteTransfer | None = None):
        # We only have to check source user if it's new - and source already has a unique check
        qs = self.meeting.vote_transfers
        if modifying:
            qs = qs.exclude(pk=modifying.pk)
        if qs.filter(models.Q(target=target) | models.Q(source=target)).exists():
            raise ValidationError(
                {
                    "target": f"User {target} already delegates their vote or has received a vote from someone else."
                }
            )
        # Source and target must have intersecting groups
        if (
            not self.meeting.groups.filter(
                memberships__user=source, memberships__role__role_id=MAIN_ROLE_ID
            )
            .filter(
                memberships__user=target, memberships__role__role_id=SUBSTITUTE_ROLE_ID
            )
            .exists()
        ):
            raise ValidationError(
                {
                    "target": "Source and target user must have roles within the same group."
                }
            )


@er_policy
class MainSubstDelegatePolicy(ElectoralRegisterPolicy):
    """
    Main role users have votes and can delegate them to subst.

    """

    name = "main_subst_delegate"
    title = _("Main/Substitute + delegate votes")
    description = _(
        "Group members with main have votes and can delegate them to users with substitute role. "
        "Electoral registries will be created and assigned when a poll starts."
    )
    logger = logger
    handles_vote_weight = False
    available = False  # Not installed manually
    allow_trigger = True
    handles_active_check = False
    vote_transfer_policy = MainAndSubstVT.name

    def get_voters(self, update_memberships=False, **kwargs) -> dict[int, int]:
        voters = set(self.meeting.get_userids_with_roles(ROLE_POTENTIAL_VOTER))
        for vt in self.meeting.vote_transfers.filter(source_id__in=voters):
            voters.add(vt.target_id)
            voters.remove(vt.source_id)
        return {x: 1 for x in voters}

    def pre_apply(self, poll: Poll, target: str):
        self.create_er()  # Won't trigger unless needed
