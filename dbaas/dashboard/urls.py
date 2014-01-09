from django.conf.urls.defaults import *
from django.conf import settings

urlpatterns = patterns('dashboard.views',
    url(r"^$", "dashboard", name="dashboard.index"),
    url(r'^databaseinfra/(?P<infra_id>\d+)/?$', 'databaseinfra', name='databaseinfra.index'),
    
)