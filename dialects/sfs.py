from envelope.core.message import ContextAction
from envelope.messages.common import Status
from envelope.messages.errors import BadRequestError
from envelope.utils import websocket_send
from voteit.meeting.models import MeetingGroup
from voteit.meeting.permissions import MeetingGroupPermissions
from voteit.meeting.permissions import MeetingPermissions
from voteit.poll.app.er_policies.group_votes_before_poll import GroupVotesBeforePoll
from voteit.poll.schemas import VotersWeightsSchema

DELEGATION_LEADER_ROLE_ID = "leader"


class SFSSetDelegationVotersSchema(VotersWeightsSchema):
    meeting_group: int


class SFSSetDelegationVoters(ContextAction):
    name = "sfs.set_delegation_voters"
    permission = MeetingGroupPermissions.VIEW
    model = MeetingGroup
    context_schema_attr = "meeting_group"
    schema = SFSSetDelegationVotersSchema
    data: SFSSetDelegationVotersSchema

    def run_job(self):
        self.assert_perm()
        meeting_group: MeetingGroup = self.context
        # Does the group have votes?
        if not meeting_group.votes:
            raise BadRequestError.from_message(
                self,
                msg="This group has no votes.",
            )
        # Correct amount of votes set?
        total_dist_votes = sum(x.weight for x in self.data.weights)
        if total_dist_votes != meeting_group.votes:
            raise BadRequestError.from_message(
                self,
                msg=f"Bad vote sum. You've set {total_dist_votes} but "
                f"the group has {meeting_group.votes} votes.",
            )
        meeting = meeting_group.meeting
        # Correct ER policy?
        if meeting.er_policy_name != GroupVotesBeforePoll.name:
            raise BadRequestError.from_message(
                self,
                msg=f"This message is only valid while using {GroupVotesBeforePoll.name} electoral register policy.",
            )
        # Delegation leader or moderator?
        if not (
            meeting_group.memberships.filter(
                user=self.user, role__role_id=DELEGATION_LEADER_ROLE_ID
            ).exists()
            or self.user.has_perm(MeetingPermissions.CHANGE, meeting)
        ):
            raise BadRequestError.from_message(
                self,
                msg="You're not delegation leader or moderator.",
            )
        # Check that these users are members of the group
        user_pks = {x.user for x in self.data.weights}
        group_member_pks = set(meeting_group.members.all().values_list("pk", flat=True))
        non_members = user_pks - group_member_pks
        if non_members:
            raise BadRequestError.from_message(
                self,
                msg=f"The following user PKs aren't members of that group: {', '.join(str(x) for x in non_members)}.",
            )
        for vw in self.data.weights:
            meeting_group.memberships.update_or_create(
                user_id=vw.user,
                defaults={"votes": vw.weight},
            )
        response = Status.from_message(self)
        websocket_send(response, state=response.SUCCESS)
        return response
