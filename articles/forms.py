import re

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import UserPreference, BlogPostingAccount

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


class UserContactForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email']
        labels = {'email': '이메일'}
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

    def clean_email(self):
        email = self.cleaned_data['email']
        if email and User.objects.exclude(pk=self.instance.pk).filter(email=email).exists():
            raise forms.ValidationError("이미 다른 계정에서 사용 중인 이메일입니다.")
        return email


class UserPreferenceForm(forms.ModelForm):
    class Meta:
        model = UserPreference
        fields = ['phone_number', 'news_subscription', 'interested_keywords', 'post_all_articles', 'auto_posting_enabled']
        labels = {
            'phone_number': '전화번호',
            'news_subscription': '뉴스 구독',
            'interested_keywords': '관심 키워드',
            'post_all_articles': '전체 발행(관심 키워드 무시하고 모든 기사 발행)',
            'auto_posting_enabled': '자동 포스팅 사용',
        }
        widgets = {
            'news_subscription': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'post_all_articles': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'auto_posting_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '010-1234-5678'}),
            'interested_keywords': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '예: 반도체, 2차전지, 인공지능 (콤마로 구분)',
            }),
        }

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get('phone_number', '').strip()
        if phone_number and not PHONE_NUMBER_RE.match(phone_number):
            raise forms.ValidationError("올바른 전화번호 형식이 아닙니다. 예: 010-1234-5678")
        return phone_number


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
            'account_id': forms.TextInput(attrs={'class': 'form-control'}),
            # 저장된 값은 보안상 다시 화면에 노출하지 않고, 새로 입력했을 때만 갱신
            'credential': forms.PasswordInput(
                render_value=False, attrs={'class': 'form-control', 'placeholder': '변경 시에만 입력'}
            ),
        }
