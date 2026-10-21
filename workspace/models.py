"""A user's saved work: the data the app pages used to keep only in browser storage."""
from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class Site(models.Model):
    """A website the user works on (the "+ Add a website" project in the app)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sites")
    host = models.CharField(max_length=253, help_text="Hostname without www., e.g. example.com")
    url = models.URLField(max_length=2000)
    name = models.CharField(max_length=150, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated"]
        constraints = [models.UniqueConstraint(fields=["user", "host"], name="site_unique_host_per_user")]

    def __str__(self):
        return self.host


class Audit(models.Model):
    """One website audit run. The full engine result is kept so the report can be reopened."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="audits")
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="audits", null=True, blank=True)
    url = models.URLField(max_length=2000)
    final_url = models.URLField(max_length=2000)
    keyword = models.CharField(max_length=200, blank=True)
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    critical = models.PositiveIntegerField(default=0)
    warning = models.PositiveIntegerField(default=0)
    notice = models.PositiveIntegerField(default=0)
    passed = models.PositiveIntegerField(default=0)
    pages = models.PositiveIntegerField(default=0)
    broken_links = models.PositiveIntegerField(default=0)
    result = models.JSONField()
    created = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.final_url} ({self.created:%Y-%m-%d})"


class KeywordList(models.Model):
    """A Keyword Manager list."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="keyword_lists")
    name = models.CharField(max_length=120)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="keywordlist_unique_name_per_user")]

    def __str__(self):
        return self.name


class Keyword(models.Model):
    keyword_list = models.ForeignKey(KeywordList, on_delete=models.CASCADE, related_name="keywords")
    kw = models.CharField("keyword", max_length=200)
    data = models.JSONField(default=dict, blank=True, help_text="Metrics saved with it: volume, difficulty, intent…")
    added = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["added", "id"]
        constraints = [models.UniqueConstraint(fields=["keyword_list", "kw"], name="keyword_unique_in_list")]

    def __str__(self):
        return self.kw


class RankSnapshot(models.Model):
    """One Search Console export imported in Position Tracking (one per date)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="rank_snapshots")
    date = models.DateField()
    queries = models.JSONField(null=True, blank=True)
    pages = models.JSONField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="ranksnapshot_unique_date_per_user")]

    def __str__(self):
        return f"{self.date}"


class Campaign(models.Model):
    """A target keyword + location + landing page, from the Campaigns page."""

    class Status(models.TextChoices):
        ACTIVE = "Active"
        PAUSED = "Paused"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campaigns")
    client_id = models.CharField(max_length=40, blank=True, help_text="The id the Campaigns page gave it")
    name = models.CharField(max_length=200)
    brand = models.CharField(max_length=150)
    location = models.CharField(max_length=150, blank=True)
    url = models.URLField(max_length=2000)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    backlinks = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.name


class Preferences(models.Model):
    """Settings page defaults, e.g. {"gl": "in"} for the default country."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="preferences")
    data = models.JSONField(default=dict, blank=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "preferences"


class SavedState(models.Model):
    """Per-tool saved state (writing drafts, AI visibility prompts, local SEO profile, report options),
    stored under the same keys the pages use today, e.g. "scoutly.draft"."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_states")
    key = models.CharField(max_length=64)
    data = models.JSONField()
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="savedstate_unique_key_per_user")]

    def __str__(self):
        return self.key
