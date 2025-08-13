from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.reverse import reverse
from rest_framework.test import APITestCase
from voteit.active.components import ActiveUsersComponent
from voteit.core.workflows import EnabledWf
from voteit.meeting.models import GroupMembership
from voteit.meeting.models import GroupRole
from voteit.meeting.models import Meeting
from voteit.meeting.models import MeetingGroup
from voteit.meeting.roles import ROLE_DISCUSSER
from voteit.meeting.roles import ROLE_PARTICIPANT
from voteit.meeting.roles import ROLE_POTENTIAL_VOTER

from dialects.main_subst_delegate import MAIN_ROLE_ID
from dialects.main_subst_delegate import SUBSTITUTE_ROLE_ID
from dialects.main_subst_delegate import MainSubstDelegatePolicy

from voteit.meeting.roles import ROLE_PROPOSER

User = get_user_model()


class FixtureMixin:

    @classmethod
    def add_fixture(cls):
        cls.meeting: Meeting = Meeting.objects.create(
            er_policy_name=MainSubstDelegatePolicy.name,
            group_roles_active=True,
        )
        # Roles
        cls.main_role: GroupRole = cls.meeting.group_roles.create(
            role_id=MAIN_ROLE_ID,
            roles=[ROLE_POTENTIAL_VOTER, ROLE_PROPOSER, ROLE_DISCUSSER],
        )
        cls.subst_role: GroupRole = cls.meeting.group_roles.create(
            role_id=SUBSTITUTE_ROLE_ID,
            roles=[ROLE_PROPOSER, ROLE_DISCUSSER],
        )
        # Groups
        cls.the_voters_group: MeetingGroup = cls.meeting.groups.create(
            groupid="the_voters"
        )
        cls.the_other_voters_group: MeetingGroup = cls.meeting.groups.create(
            groupid="the_other_voters"
        )
        cls.the_board: MeetingGroup = cls.meeting.groups.create(groupid="board")
        # Users
        cls.president = User.objects.create(username="president")
        cls.main1 = User.objects.create(username="one")
        cls.main2 = User.objects.create(username="two")
        cls.subst3 = User.objects.create(username="three")
        cls.subst4 = User.objects.create(username="four")
        # cls.outsider = User.objects.create(username="outsider")
        users = [cls.president, cls.main1, cls.main2, cls.subst3, cls.subst4]
        for user in users:
            cls.meeting.add_roles(user, ROLE_PARTICIPANT)
        # Memberships
        cls.mem_president: GroupMembership = cls.the_board.memberships.create(
            user=cls.president,
        )
        cls.mem_one: GroupMembership = cls.the_voters_group.memberships.create(
            user=cls.main1, role=cls.main_role
        )
        cls.mem_two: GroupMembership = cls.the_voters_group.memberships.create(
            user=cls.main2, role=cls.main_role
        )
        cls.mem_three: GroupMembership = cls.the_voters_group.memberships.create(
            user=cls.subst3, role=cls.subst_role
        )
        cls.mem_four: GroupMembership = cls.the_voters_group.memberships.create(
            user=cls.subst4, role=cls.subst_role
        )
        # Enable active component
        cls.component = cls.meeting.components.create(
            component_name=ActiveUsersComponent.name, state=EnabledWf.ON
        )
        # Set active users
        for user in users:
            cls.meeting.active_users.create(user=user)


class MainAndSubstDelegateTests(TestCase, FixtureMixin):
    @classmethod
    def setUpTestData(cls):
        cls.add_fixture()

    def test_unmodified(self):
        self.assertEqual(
            {self.main1.pk: 1, self.main2.pk: 1}, self.meeting.er_policy.get_voters()
        )

    def test_transfer(self):
        self.meeting.vote_transfers.create(source=self.main1, target=self.subst3)
        self.assertEqual(
            {self.subst3.pk: 1, self.main2.pk: 1}, self.meeting.er_policy.get_voters()
        )


class MainAndSubstDelegateVTTests(APITestCase, FixtureMixin):
    @classmethod
    def setUpTestData(cls):
        cls.add_fixture()

    def test_transfer(self):
        self.client.force_login(self.main1)
        url = reverse("vote-transfer-list")
        data = {
            "meeting": self.meeting.pk,
            "source": self.main1.pk,
            "target": self.subst3.pk,
        }
        response = self.client.post(url, data=data)
        self.assertEqual(201, response.status_code)
        self.assertEqual(
            {self.subst3.pk: 1, self.main2.pk: 1}, self.meeting.er_policy.get_voters()
        )

    def test_subst_transfer_existing(self):
        transfer = self.meeting.vote_transfers.create(
            source=self.main1, target=self.subst3
        )
        self.client.force_login(self.subst3)
        url = reverse("vote-transfer-detail", kwargs={"pk": transfer.pk})
        response = self.client.patch(url, data={"target": self.subst4.pk})
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {self.subst4.pk: 1, self.main2.pk: 1}, self.meeting.er_policy.get_voters()
        )

    def test_main_to_other_main(self):
        transfer = self.meeting.vote_transfers.create(
            source=self.main1, target=self.subst3
        )
        self.client.force_login(self.main1)
        url = reverse("vote-transfer-detail", kwargs={"pk": transfer.pk})
        response = self.client.patch(url, data={"target": self.main2.pk})
        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "target": [
                    "Source and target user must have roles within the same group."
                ]
            },
            response.json(),
        )
