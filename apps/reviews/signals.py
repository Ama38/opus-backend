from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Review
from .services import recalculate_master_rating


@receiver(post_save, sender=Review, dispatch_uid="reviews.recalculate_master_rating.save")
def update_master_rating_after_save(sender, instance: Review, **kwargs):
    recalculate_master_rating(instance.target_id)


@receiver(post_delete, sender=Review, dispatch_uid="reviews.recalculate_master_rating.delete")
def update_master_rating_after_delete(sender, instance: Review, **kwargs):
    recalculate_master_rating(instance.target_id)
