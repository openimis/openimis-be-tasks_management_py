from api_fhir_r4.utils import DbManagerUtils
from core.forms import User
from core.services import create_or_update_interactive_user, create_or_update_core_user


class LogInHelper:
    _TEST_USER_NAME = "TestUserTest2"
    _TEST_USER_PASSWORD = "TestPasswordTest2"
    _TEST_DATA_USER = {
        "username": _TEST_USER_NAME,
        "last_name": _TEST_USER_NAME,
        "password": _TEST_USER_PASSWORD,
        "other_names": _TEST_USER_NAME,
        "user_types": "INTERACTIVE",
        "language": "en",
        "roles": [1, 3, 5, 9],
    }

    _TEST_USER_NAME_TASK_EXECUTOR = "TestUserTestTaskExecutor"
    _TEST_USER_PASSWORD_TASK_EXECUTOR = "TestPasswordTestTaskExecutor"
    _TEST_DATA_USER_TASK_EXECUTOR = {
        "username": _TEST_USER_NAME_TASK_EXECUTOR,
        "last_name": _TEST_USER_NAME_TASK_EXECUTOR,
        "password": _TEST_USER_PASSWORD_TASK_EXECUTOR,
        "other_names": _TEST_USER_NAME_TASK_EXECUTOR,
        "user_types": "INTERACTIVE",
        "language": "en",
        "roles": [1, 3, 5, 9],
    }

    def get_or_create_user_api(self):
        user = User.objects.filter(username=self._TEST_USER_NAME).first()
        if user is None:
            user = self.__create_user_interactive_core()
        return user

    def __create_user_interactive_core(self):
        i_user, i_user_created = create_or_update_interactive_user(
            user_id=None, data=self._TEST_DATA_USER, audit_user_id=999, connected=False)
        create_or_update_core_user(
            user_uuid=None, username=self._TEST_DATA_USER["username"], i_user=i_user)
        return DbManagerUtils.get_object_or_none(User, username=self._TEST_USER_NAME)

    def get_or_create_task_executor_api(self):
        user = User.objects.filter(username=self._TEST_USER_NAME_TASK_EXECUTOR).first()
        if user is None:
            user = self.__create_user_interactive_core()
        return user

    def __create_user_interactive_core_task_executor(self):
        i_user, i_user_created = create_or_update_interactive_user(
            user_id=None, data=self._TEST_DATA_USER_TASK_EXECUTOR, audit_user_id=999, connected=False)
        create_or_update_core_user(
            user_uuid=None, username=self._TEST_DATA_USER_TASK_EXECUTOR["username"], i_user=i_user)
        return DbManagerUtils.get_object_or_none(User, username=self._TEST_USER_NAME_TASK_EXECUTOR)
