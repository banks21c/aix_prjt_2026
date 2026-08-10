import re

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import (
    UserPreference, BlogPostingAccount, NewsKeyword, NewsletterSubscriber, AnalyzedArticle,
    SubscriptionOrder,
)

# 하이픈 유무 모두 허용하는 국내 전화번호 형식 (휴대폰 010~019, 서울 02, 그 외 지역 0XX 유선)
PHONE_NUMBER_RE = re.compile(r'^0\d{1,2}-?\d{3,4}-?\d{4}$')


class SignUpForm(forms.Form):
    username = forms.CharField(max_length=150, label="아이디")
    email = forms.EmailField(label="이메일")
    password1 = forms.CharField(widget=forms.PasswordInput, label="비밀번호")
    password2 = forms.CharField(widget=forms.PasswordInput, label="비밀번호 확인")

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("이미 사용 중인 아이디입니다.")
        return username

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("이미 가입된 이메일입니다.")
        return email

    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        if password1:
            validate_password(password1)  # settings.AUTH_PASSWORD_VALIDATORS 규칙 그대로 적용
        return password1

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', "비밀번호가 일치하지 않습니다.")
        return cleaned_data


class LoginForm(AuthenticationForm):
    # 이메일 인증 전(is_active=False)에는 로그인을 막고, 원인을 알 수 있게 한국어 메시지로 안내
    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise forms.ValidationError(
                "이메일 인증이 완료되지 않은 계정입니다. 가입 시 받은 메일에서 인증을 완료해주세요.",
                code='inactive',
            )


class UserContactForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'email']
        labels = {'first_name': '이름', 'email': '이메일'}
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '홍길동'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

    def clean_email(self):
        email = self.cleaned_data['email']
        if email and User.objects.exclude(pk=self.instance.pk).filter(email=email).exists():
            raise forms.ValidationError("이미 다른 계정에서 사용 중인 이메일입니다.")
        return email


class UserPreferenceForm(forms.ModelForm):
    # interested_keywords는 모델상 콤마구분 CharField이지만, 관리자가 등록해둔 NewsKeyword 범위
    # 밖의 키워드는 애초에 수집되지 않아 뉴스가 하나도 안 잡히므로(=조용한 실패), 자유 입력 대신
    # 활성 NewsKeyword 중에서만 체크박스로 고르게 한다. Meta.fields에는 넣지 않고 save()에서
    # 직접 콤마구분 문자열로 변환해서 저장한다.
    interested_keywords = forms.ModelMultipleChoiceField(
        queryset=NewsKeyword.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='관심 키워드',
    )

    class Meta:
        model = UserPreference
        fields = ['phone_number', 'news_subscription', 'post_all_articles', 'auto_posting_enabled']
        labels = {
            'phone_number': '전화번호',
            'news_subscription': '뉴스 구독',
            'post_all_articles': '전체 발행(관심 키워드 무시하고 모든 기사 발행)',
            'auto_posting_enabled': '자동 포스팅 사용',
        }
        widgets = {
            'news_subscription': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'post_all_articles': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'auto_posting_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '010-1234-5678'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            selected = [kw.strip() for kw in self.instance.interested_keywords.split(',') if kw.strip()]
            self.fields['interested_keywords'].initial = NewsKeyword.objects.filter(
                is_active=True, keyword__in=selected
            )

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number', '').strip()
        if phone_number and not PHONE_NUMBER_RE.match(phone_number):
            raise forms.ValidationError("올바른 전화번호 형식이 아닙니다. 예: 010-1234-5678")
        return phone_number

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.interested_keywords = ','.join(
            kw.keyword for kw in self.cleaned_data['interested_keywords']
        )
        if commit:
            instance.save()
        return instance


class BlogAccountForm(forms.ModelForm):
    class Meta:
        model = BlogPostingAccount
        fields = ['is_enabled', 'site_url', 'account_id', 'credential']
        labels = {
            'is_enabled': '사용',
            'site_url': '사이트 주소',
            'account_id': '계정 ID',
            'credential': '비밀번호 / API Key',
        }
        widgets = {
            'is_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'site_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://내블로그.com'}),
            # autocomplete="off" — 공용 PC 등 같은 브라우저에서 다른 회원 세션으로 이 필드에
            # 입력했던 값을 브라우저가 자동완성으로 제안해, 서버가 채운 값처럼 오인될 수 있음
            # (실제 신고 사례: 계정 ID/비밀번호 칸에 다른 회원이 입력했던 값이 뜬 것처럼 보임 —
            # DB 확인 결과 서버 데이터는 비어있었고 브라우저 자동완성이 원인이었음).
            'account_id': forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
            # 저장된 값은 보안상 다시 화면에 노출하지 않고, 새로 입력했을 때만 갱신.
            # autocomplete="off"는 크롬 등 주요 브라우저의 저장된 비밀번호 자동채움에는 대부분
            # 무시되므로(문서화된 동작 — "새 비밀번호 입력" 의도를 알리는 값만 존중함),
            # "new-password"를 대신 사용한다. (my_page.html의 미끼 입력 필드와 함께 적용)
            'credential': forms.PasswordInput(
                render_value=False,
                attrs={'class': 'form-control', 'placeholder': '변경 시에만 입력', 'autocomplete': 'new-password'},
            ),
        }


class NewsletterForm(forms.Form):
    email = forms.EmailField(label="이메일")


class SubscriptionOrderForm(forms.ModelForm):
    referral_source = forms.ChoiceField(
        choices=[('', '가입 경로를 선택해주세요')] + SubscriptionOrder.REFERRAL_SOURCE_CHOICES,
        label="가입 경로",
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    motivation = forms.ChoiceField(
        choices=[('', '구독 동기를 선택해주세요')] + SubscriptionOrder.MOTIVATION_CHOICES,
        label="구독 동기",
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    # ModelForm이 CharField(choices=...)를 라디오로 만들 때, blank=False라도 기본값이 없으면
    # 자동으로 빈 선택지("---------")를 앞에 끼워 넣는다 — 라디오 버튼 목록에 아무 의미 없는
    # "---------" 항목이 하나 섞여 나오는 원인이었다. 여기서 직접 필드를 선언해(빈 접두 없이)
    # 그 자동 삽입을 피한다.
    payment_method = forms.ChoiceField(
        choices=SubscriptionOrder.PAYMENT_METHOD_CHOICES,
        label="결제 수단",
        widget=forms.RadioSelect,
    )

    class Meta:
        model = SubscriptionOrder
        fields = ['name', 'phone', 'referral_source', 'motivation', 'payment_method']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '구독자명을 입력하세요.'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': "'-' 없이 번호만 입력해주세요."}),
        }

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone and not PHONE_NUMBER_RE.match(phone):
            raise forms.ValidationError("올바른 전화번호 형식이 아닙니다. 예: 010-1234-5678")
        return phone


class NewsScrapeForm(forms.Form):
    url = forms.URLField(
        label="URL",
        widget=forms.URLInput(attrs={
            'class': 'form-control',
            'placeholder': 'https://example.com/news/12345',
        }),
    )


class NewsWriteForm(forms.Form):
    title = forms.CharField(
        label="제목",
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '제목을 입력하세요',
        }),
    )
    content = forms.CharField(
        label="내용",
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 14,
            'placeholder': '본문을 붙여넣으세요',
        }),
    )


class NewsArticleEditForm(forms.ModelForm):
    def __init__(self, *args, is_staff=False, **kwargs):
        super().__init__(*args, **kwargs)
        # is_premium(유료 회원 전용 콘텐츠 지정)은 콘텐츠 큐레이션 성격의 관리자 전용 필드라,
        # 본인 스크래핑 글을 편집하는 일반 회원에게는 아예 숨긴다.
        if not is_staff:
            self.fields.pop('is_premium', None)

    class Meta:
        model = AnalyzedArticle
        fields = [
            'title', 'source_media', 'ai_summary', 'ai_analysis', 'blog_content',
            'applied_template', 'is_premium',
        ]
        labels = {
            'title': '원본 제목',
            'source_media': '언론사',
            'ai_summary': 'AI 3줄 요약',
            'ai_analysis': 'AI 관점 분석',
            'blog_content': 'AI 요약 원고',
            'applied_template': '적용된 템플릿',
            'is_premium': '유료 회원 전용 콘텐츠',
        }
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'source_media': forms.TextInput(attrs={'class': 'form-control'}),
            'ai_summary': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'ai_analysis': forms.Textarea(attrs={'class': 'form-control', 'rows': 6}),
            'blog_content': forms.Textarea(attrs={'class': 'form-control', 'rows': 10}),
            'applied_template': forms.Select(attrs={'class': 'form-select'}),
            'is_premium': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
