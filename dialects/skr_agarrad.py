import os.path
from logging import getLogger

from django.conf import settings
from django.db import models
from pydantic import BaseModel
from pydantic import conlist
from pydantic import constr
from pydantic import validator

from voteit.meeting.dialects import DialectScript
from voteit.meeting.models import GroupMembership
from voteit.meeting.models import Meeting
from voteit.meeting.models import MeetingGroup
from voteit.poll.abcs import ElectoralRegisterPolicy
from voteit.poll.exceptions import ElectoralRegisterError
from voteit.poll.models import Poll
from voteit.poll.registries import er_policy

logger = getLogger(__name__)
SKR_GROUP_ID = "skr"
REGION_TAG = "region"
KOMMUN_TAG = "kommun"
FILE_REGIONER = "regioner.tsv"
FILE_KOMMUNER = "agarrad_kommuner.tsv"


class CSVRows(BaseModel):
    rows: conlist(
        conlist(
            constr(to_lower=True, strip_whitespace=True),
            min_items=2,
            max_items=2,
        ),
        min_items=1,
        max_items=500,
    )

    @validator("rows", pre=True, each_item=True)
    def transform_rows(cls, v: list[str] | str):
        if isinstance(v, str):
            return v.split("\t")
        return v


class CreateSKRGroups(DialectScript):
    def install(self, meeting: Meeting):
        regioner_file = os.path.join(
            settings.MEETING_DIALECTS_DIR, "data", FILE_REGIONER
        )
        kommuner_file = os.path.join(
            settings.MEETING_DIALECTS_DIR, "data", FILE_KOMMUNER
        )
        objs = [MeetingGroup(groupid="skr", meeting=meeting, title="SKR")]
        objs.extend(self.mk_bulk_objs(regioner_file, REGION_TAG, meeting))
        objs.extend(self.mk_bulk_objs(kommuner_file, KOMMUN_TAG, meeting))
        MeetingGroup.objects.bulk_create(objs)

    def mk_bulk_objs(self, fn, tag, meeting):
        with open(fn, "r") as f:
            data = CSVRows(rows=f.readlines())
            for row in data.rows:
                yield MeetingGroup(
                    meeting=meeting, groupid=row[0], title=row[1], tags=[tag]
                )
            # In case this dialect is ever installable for an existing meeting, we may need to change this
            # for row in data.rows:
            #     meeting.groups.update_or_create(
            #         groupid=row[0], defaults={"title": row[1], "tags": [tag]}
            #     )


@er_policy
class SKRAgarradERP(ElectoralRegisterPolicy):
    name = "skr_agarrad"
    title = "SKRs ägarråd"
    description = (
        "Bygger på import av kommuner, regioner och SKR-grupp. "
        "Taggar på grupperna används."
    )
    logger = logger
    handles_vote_weight = True
    available = False
    allow_trigger = True

    def get_voters(self, update_memberships=False, **kwargs) -> dict[int, int]:
        skr = self.meeting.groups.filter(groupid=SKR_GROUP_ID).first()
        if not skr:
            raise ElectoralRegisterError(
                "Bad configuration, SKR Group not found. This should never be used without the correct meeting dialect."
            )
        kommun_groups_qs = self.meeting.groups.filter(tags__contains=[KOMMUN_TAG])
        region_groups_qs = self.meeting.groups.filter(tags__contains=[REGION_TAG])
        intersection = kommun_groups_qs & region_groups_qs
        if intersection.exists():
            raise ElectoralRegisterError(
                "%s group(s) contained both 'kommun' and 'region' tag."
                % intersection.count()
            )
        # Build a vote weight dict first. We'll transfer the vote weight to a specific user later on.
        combined_groups = kommun_groups_qs | region_groups_qs
        no_delegations_qs = combined_groups.filter(delegate_to__isnull=True).annotate(
            incoming=models.Count("delegations_from")
        )
        groups_to_vote_weight = {
            x.pk: getattr(x, "incoming", 0) + 1 for x in no_delegations_qs
        }

        memberships = GroupMembership.objects.filter(
            meeting_group__in=kommun_groups_qs | region_groups_qs
        ).filter(user__in=self.meeting.active_users.values_list("user_id", flat=True))
        group_to_user = {}
        for membership in memberships:
            if membership.meeting_group_id in group_to_user:
                logger.warning(
                    "A meeting group for %s has more users than 1. SKRs dialect doesn't work well with that.",
                    self.meeting,
                )
                continue
            group_to_user[membership.meeting_group_id] = membership.user_id
        voters = {
            group_to_user[g]: groups_to_vote_weight[g]
            for g in group_to_user
            if groups_to_vote_weight.get(g)
        }
        skr_user = skr.members.first()
        if skr_user:
            skr_vw = sum(voters.values()) + skr.delegations_from.count() * 2 - 1
            if skr_user.pk in voters:
                raise ElectoralRegisterError("SKR user found in another group")
            voters[skr_user.pk] = skr_vw
        return voters

    def pre_apply(self, poll: Poll, target: str):
        self.create_er()  # Won't trigger unless needed
