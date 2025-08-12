from __future__ import annotations

from collections import defaultdict
from logging import getLogger
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from voteit.meeting.models import GroupMembership
from voteit.poll.abcs import ElectoralRegisterPolicy
from voteit.poll.exceptions import ElectoralRegisterError
from voteit.poll.registries import er_policy


if TYPE_CHECKING:
    from voteit.poll.models import Poll
    from voteit.meeting.models import GroupRole

__all__ = ("MainSubstActivePolicy",)

logger = getLogger(__name__)

User = get_user_model()

MAIN_ROLE_ID = "main"
SUBSTITUTE_ROLE_ID = "substitute"


@er_policy
class MainSubstActivePolicy(ElectoralRegisterPolicy):
    """
    Instructions:
    - Set total number of votes on a group.
    - Set role main or substitute on members of that group. (That should trigger potential voter too)
    - Users set themselves as active.
    - When a poll starts or becomes upcoming, the main users get one vote each, if there are any spare
    votes left they're distributed to substitutes in chronological order of when they became active.
    """

    name = "main_subst_active"
    title = _("Main/Substitute + active")
    description = _(
        "Voters assigned depending on number of votes in groups. Votes are assigned to main first, "
        "then substitutes if there are any left. "
        "If there aren't enough votes for everyone, "
        "they're assigned in the order of when users became active. "
        "Electoral registries will be created and assigned when a poll starts."
    )
    logger = logger
    handles_vote_weight = False
    available = False  # Not installed manually
    allow_trigger = True
    handles_active_check = True

    def get_voters(self, update_memberships=False, **kwargs) -> dict[int, int]:
        relevant_roles = list(
            self.meeting.group_roles.filter(
                role_id__in=[MAIN_ROLE_ID, SUBSTITUTE_ROLE_ID]
            ).order_by("role_id")
        )
        if len(relevant_roles) != 2:
            raise ElectoralRegisterError(
                "Bad configuration, wrong roles returned. This should never be used without the correct meeting dialect."
            )
        main_role: GroupRole = relevant_roles[0]
        subst_role = relevant_roles[1]
        groups_with_votes = self.meeting.groups.filter(votes__gt=0)
        group_vote_power = {x: x.votes for x in groups_with_votes}
        picked_voters: set[int] = set()
        active_user_pks = list(
            self.meeting.active_users.order_by("created").values_list(
                "user_id", flat=True
            )
        )
        groups_vote_dist = defaultdict(set)
        for role in [main_role, subst_role]:
            for group in groups_with_votes:
                # May have been exhausted
                if not group_vote_power[group]:
                    continue
                memberships = group.memberships.filter(
                    user__pk__in=active_user_pks, role=role
                )
                members = sorted(
                    memberships.values_list("user_id", flat=True),
                    key=lambda x: active_user_pks.index(x),
                )
                # Distribute votes
                for user_pk in members:
                    if not group_vote_power[group]:
                        break
                    if user_pk in picked_voters:
                        continue
                    # User should be voter
                    picked_voters.add(user_pk)
                    group_vote_power[group] -= 1
                    groups_vote_dist[group].add(user_pk)
        # And finally update GroupMembership objects vote distribution (to signal why a user has a vote)
        if update_memberships:
            for group, user_pks in groups_vote_dist.items():
                # Needs to have a vote
                for membership in group.memberships.filter(
                    user_id__in=user_pks
                ).exclude(votes=1):
                    membership.votes = 1
                    membership.save()
                # Should not have a vote
                for membership in group.memberships.exclude(
                    user_id__in=user_pks
                ).filter(votes__isnull=False):
                    # Update this (slow) way to trigger events - can be optimized later on
                    membership.votes = None
                    membership.save()
            # Make sure no other groups have votes
            for membership in GroupMembership.objects.filter(
                meeting_group__meeting=self.meeting, votes__gt=0
            ).exclude(meeting_group__in=groups_vote_dist.keys()):
                # Update this (slow) way to trigger events - can be optimized later on
                membership.votes = None
                membership.save()
        return {x: 1 for x in picked_voters}

    def pre_apply(self, poll: Poll, target: str):
        self.create_er()  # Won't trigger unless needed
