from rest_framework import decorators, response, status, viewsets

from .models import SupportCase
from .serializers import SupportCaseSerializer, SupportMessageCreateSerializer, SupportMessageSerializer
from .services import add_support_message


class SupportCaseViewSet(viewsets.ModelViewSet):
    serializer_class = SupportCaseSerializer

    def get_queryset(self):
        queryset = SupportCase.objects.select_related("user", "order", "assigned_to").prefetch_related("messages__sender")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @decorators.action(detail=True, methods=["post"])
    def message(self, request, pk=None):
        case = self.get_object()
        serializer = SupportMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = add_support_message(
            case,
            sender=request.user,
            text=serializer.validated_data["text"],
        )
        return response.Response({"message": SupportMessageSerializer(message).data}, status=status.HTTP_201_CREATED)

