import json

from django.core.cache import cache
from django.test import Client, TestCase

from workspace.models import Site

from .models import User

PW = "a-long-test-passphrase"


def post(client, path, data=None, method="post", **headers):
    return getattr(client, method)(path, data=json.dumps(data or {}), content_type="application/json", **headers)


class AccountTests(TestCase):
    def setUp(self):
        cache.clear()

    def signup(self, email="ana@example.com", password=PW, name="Ana"):
        return post(self.client, "/api/auth/signup", {"name": name, "email": email, "password": password})

    def test_signup_logs_in(self):
        r = self.signup(email="Ana@Example.com ")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["user"]["email"], "ana@example.com")
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["name"], "Ana")

    def test_signup_validation(self):
        self.assertEqual(self.signup(password="123").status_code, 400)
        self.assertEqual(self.signup(email="not-an-email").status_code, 400)
        self.assertEqual(post(self.client, "/api/auth/signup", {"email": "x@example.com", "password": PW}).status_code, 400)
        self.signup()
        self.client.logout()
        r = self.signup()
        self.assertEqual(r.status_code, 409)

    def test_login_logout(self):
        User.objects.create_user("ana@example.com", PW, name="Ana")
        self.assertEqual(post(self.client, "/api/auth/login", {"email": "ana@example.com", "password": "wrong"}).status_code, 401)
        r = post(self.client, "/api/auth/login", {"email": "ANA@example.com", "password": PW})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(post(self.client, "/api/auth/logout").status_code, 200)
        self.assertIsNone(self.client.get("/api/auth/me").json()["user"])

    def test_login_locks_after_repeated_failures(self):
        User.objects.create_user("ana@example.com", PW, name="Ana")
        for _ in range(8):
            post(self.client, "/api/auth/login", {"email": "ana@example.com", "password": "wrong"})
        r = post(self.client, "/api/auth/login", {"email": "ana@example.com", "password": PW})
        self.assertEqual(r.status_code, 429)

    def test_csrf_is_enforced(self):
        c = Client(enforce_csrf_checks=True)
        r = post(c, "/api/auth/signup", {"name": "Ana", "email": "ana@example.com", "password": PW})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json()["csrf"])
        token = c.get("/api/auth/csrf").json()["csrfToken"]
        r = post(c, "/api/auth/signup", {"name": "Ana", "email": "ana@example.com", "password": PW}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 201)

    def test_update_profile_and_password(self):
        self.signup()
        r = post(self.client, "/api/auth/me", {"name": "Ana N"}, method="patch")
        self.assertEqual(r.json()["user"]["name"], "Ana N")
        self.assertEqual(post(self.client, "/api/auth/password", {"current": "nope", "new": "another-long-pass"}).status_code, 403)
        self.assertEqual(post(self.client, "/api/auth/password", {"current": PW, "new": "another-long-pass"}).status_code, 200)
        self.assertIsNotNone(self.client.get("/api/auth/me").json()["user"])  # still logged in

    def test_delete_account_removes_data(self):
        self.signup()
        user = User.objects.get()
        Site.objects.create(user=user, host="example.com", url="https://example.com")
        self.assertEqual(post(self.client, "/api/auth/me", {"password": "wrong"}, method="delete").status_code, 403)
        self.assertEqual(post(self.client, "/api/auth/me", {"password": PW}, method="delete").status_code, 200)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Site.objects.exists())
