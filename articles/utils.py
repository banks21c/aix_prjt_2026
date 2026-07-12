def get_client_ip(request):
    """프록시(X-Forwarded-For) 뒤에 있는 경우까지 고려해 실제 접속 IP를 추출."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
