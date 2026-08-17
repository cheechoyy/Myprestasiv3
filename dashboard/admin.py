from django.contrib import admin
from .models import UserProfile

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    # Kemas kini senarai paparan dengan medan baharu
    list_display = ('user', 'role', 'department', 'hod_scope', 'assigned_state', 'assigned_facility')
    
    # Tambah fungsi filter di panel admin
    list_filter = ('role', 'department', 'hod_scope')
    
    # Tambah fungsi carian
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'assigned_facility')