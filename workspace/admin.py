from django.contrib import admin

from .models import Audit, Campaign, Keyword, KeywordList, Preferences, RankSnapshot, SavedState, Site


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("host", "user", "name", "updated")
    search_fields = ("host", "name", "user__email")
    raw_id_fields = ("user",)


@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("final_url", "user", "score", "critical", "warning", "notice", "pages", "created")
    list_filter = ("created",)
    search_fields = ("final_url", "keyword", "user__email")
    raw_id_fields = ("user", "site")
    readonly_fields = ("created",)


class KeywordInline(admin.TabularInline):
    model = Keyword
    extra = 0


@admin.register(KeywordList)
class KeywordListAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "updated")
    search_fields = ("name", "user__email")
    raw_id_fields = ("user",)
    inlines = [KeywordInline]


@admin.register(RankSnapshot)
class RankSnapshotAdmin(admin.ModelAdmin):
    list_display = ("date", "user", "created")
    search_fields = ("user__email",)
    raw_id_fields = ("user",)


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "brand", "user", "status", "location", "created")
    list_filter = ("status",)
    search_fields = ("name", "brand", "user__email")
    raw_id_fields = ("user",)


@admin.register(Preferences)
class PreferencesAdmin(admin.ModelAdmin):
    list_display = ("user", "updated")
    raw_id_fields = ("user",)


@admin.register(SavedState)
class SavedStateAdmin(admin.ModelAdmin):
    list_display = ("key", "user", "updated")
    search_fields = ("key", "user__email")
    raw_id_fields = ("user",)
