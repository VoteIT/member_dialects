from django.contrib.auth import get_user_model
from django.test import TestCase

from dialects.sfs import DELEGATION_LEADER_ROLE_ID
from envelope.messages.errors import BadRequestError
from envelope.messages.errors import UnauthorizedError
from voteit.meeting.models import GroupMembership
from voteit.meeting.models import GroupRole
from voteit.meeting.models import Meeting
from voteit.meeting.models import MeetingGroup
from voteit.meeting.roles import ROLE_MODERATOR
from voteit.meeting.roles import ROLE_PARTICIPANT
from voteit.meeting.roles import ROLE_POTENTIAL_VOTER
from voteit.poll.app.er_policies.group_votes_before_poll import GroupVotesBeforePoll

User = get_user_model()


class SFSTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.meeting: Meeting = Meeting.objects.create(
            er_policy_name=GroupVotesBeforePoll.name,
            group_votes_active=True,
            group_roles_active=True,
        )
        cls.leader_role: GroupRole = cls.meeting.group_roles.create(
            role_id=DELEGATION_LEADER_ROLE_ID,
            roles=[ROLE_POTENTIAL_VOTER],
        )
        cls.doctor_hats: MeetingGroup = cls.meeting.groups.create(
            groupid="doctor_hats", votes=4
        )
        cls.moderator = User.objects.create(username="moderator")
        cls.meeting.add_roles(cls.moderator, ROLE_MODERATOR, ROLE_POTENTIAL_VOTER)
        cls.lead_hat = User.objects.create(username="lead_hat")
        cls.hangaround = User.objects.create(username="hangaround")
        cls.meeting.add_roles(cls.hangaround, ROLE_PARTICIPANT, ROLE_POTENTIAL_VOTER)
        cls.mem_lead_hat: GroupMembership = cls.doctor_hats.memberships.create(
            user=cls.lead_hat, role=cls.leader_role
        )
        cls.mem_hangaround: GroupMembership = cls.doctor_hats.memberships.create(
            user=cls.hangaround
        )

    def setUp(self):
        self.meeting.refresh_from_db()

    @property
    def _cut(self):
        from dialects.sfs import SFSSetDelegationVoters

        return SFSSetDelegationVoters

    def _mk_message(self, user, weights: list = [], **kwargs):
        kwargs.setdefault("meeting_group", self.doctor_hats.pk)
        return self._cut(
            mm={"user_pk": user.pk, "consumer_name": "abc"}, weights=weights, **kwargs
        )

    def test_set_vote_dist(self):
        msg = self._mk_message(
            self.lead_hat,
            weights=[
                {"user": self.lead_hat.pk, "weight": 2},
                {"user": self.hangaround.pk, "weight": 2},
            ],
        )
        msg.run_job()
        self.mem_lead_hat.refresh_from_db()
        self.assertEqual(2, self.mem_lead_hat.votes)
        self.mem_hangaround.refresh_from_db()
        self.assertEqual(2, self.mem_hangaround.votes)

    def test_set_vote_dist_others_cleared(self):
        self.mem_lead_hat.votes = 4
        self.mem_lead_hat.save()
        msg = self._mk_message(
            self.lead_hat,
            weights=[
                {"user": self.hangaround.pk, "weight": 4},
            ],
        )
        msg.run_job()
        self.mem_lead_hat.refresh_from_db()
        self.assertEqual(None, self.mem_lead_hat.votes)
        self.mem_hangaround.refresh_from_db()
        self.assertEqual(4, self.mem_hangaround.votes)

    def test_set_vote_dist_bad_count(self):
        msg = self._mk_message(
            self.lead_hat,
            weights=[
                {"user": self.lead_hat.pk, "weight": 10},
            ],
        )
        with self.assertRaises(BadRequestError) as cm:
            msg.run_job()
        self.assertEqual(
            {"msg": "Bad vote sum. You've set 10 but the group has 4 votes."},
            cm.exception.data.dict(),
        )

    def test_set_vote_dist_no_votes(self):
        self.doctor_hats.votes = None
        self.doctor_hats.save()
        msg = self._mk_message(
            self.lead_hat,
            weights=[
                {"user": self.lead_hat.pk, "weight": 2},
            ],
        )
        with self.assertRaises(BadRequestError) as cm:
            msg.run_job()
        self.assertEqual(
            {"msg": "This group has no votes."},
            cm.exception.data.dict(),
        )

    def test_set_vote_dist_not_potential_voter(self):
        self.meeting.remove_roles(self.hangaround, ROLE_POTENTIAL_VOTER)
        msg = self._mk_message(
            self.lead_hat,
            weights=[
                {"user": self.lead_hat.pk, "weight": 2},
                {"user": self.hangaround.pk, "weight": 2},
            ],
        )
        with self.assertRaises(BadRequestError) as cm:
            msg.run_job()
        self.assertEqual(
            {
                "msg": f"The following user PKs aren't potential voters: {self.hangaround.pk}."
            },
            cm.exception.data.dict(),
        )

    def test_unauthorized_user(self):
        outsider = User.objects.create(username="outsider")
        msg = self._mk_message(outsider)
        with self.assertRaises(UnauthorizedError) as cm:
            msg.run_job()
        self.assertEqual(
            {
                "model": "meeting.meetinggroup",
                "key": "pk",
                "value": str(self.doctor_hats.pk),
                "permission": "meeting.view_meetinggroup",
            },
            cm.exception.data.dict(),
        )

    def test_set_vote_wrong_er(self):
        self.meeting.er_policy_name = None
        self.meeting.save()
        msg = self._mk_message(
            self.lead_hat,
            weights=[
                {"user": self.lead_hat.pk, "weight": 4},
            ],
        )
        with self.assertRaises(BadRequestError) as cm:
            msg.run_job()
        self.assertEqual(
            {
                "msg": "This message is only valid while using gv_auto_before_p electoral register policy."
            },
            cm.exception.data.dict(),
        )

    def test_set_vote_regular_participant(self):
        msg = self._mk_message(
            self.hangaround,
            weights=[
                {"user": self.lead_hat.pk, "weight": 4},
            ],
        )
        with self.assertRaises(BadRequestError) as cm:
            msg.run_job()
        self.assertEqual(
            {
                "msg": "You're not delegation leader or moderator.",
            },
            cm.exception.data.dict(),
        )

    def test_set_vote_non_member(self):
        msg = self._mk_message(
            self.moderator,
            weights=[
                {"user": self.lead_hat.pk, "weight": 2},
                {"user": self.moderator.pk, "weight": 2},
            ],
        )
        with self.assertRaises(BadRequestError) as cm:
            msg.run_job()
        self.assertEqual(
            {
                "msg": f"The following user PKs aren't members of that group: {self.moderator.pk}.",
            },
            cm.exception.data.dict(),
        )
