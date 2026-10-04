import { Link, useRouter } from 'expo-router';
import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, TextInput } from 'react-native';

import { ThemedText, ThemedView } from '@/components/themed';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { signIn, signUp } from '@/lib/providers/auth';

export function AuthForm({ mode }: { mode: 'login' | 'sign-up' }) {
  const theme = useTheme();
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (mode === 'login') await signIn(email.trim(), password);
      else if (!(await signUp(email.trim(), password))) router.replace('/check-email');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const input = [styles.input, { backgroundColor: theme.backgroundElement, color: theme.text }];
  return (
    <ThemedView style={styles.container}>
      <ThemedText type="title">{mode === 'login' ? 'Trading Monitor' : 'Create account'}</ThemedText>
      <TextInput style={input} placeholder="Email" placeholderTextColor={theme.textSecondary} autoCapitalize="none"
        autoComplete="email" keyboardType="email-address" value={email} onChangeText={setEmail} />
      <TextInput style={input} placeholder="Password" placeholderTextColor={theme.textSecondary} secureTextEntry
        autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChangeText={setPassword} />
      {error && <ThemedText themeColor="negative">{error}</ThemedText>}
      <Pressable style={[styles.button, { backgroundColor: theme.accent }]} onPress={submit} disabled={busy || !email || !password}>
        {busy ? <ActivityIndicator color="#fff" /> : <ThemedText type="bold" style={{ color: '#fff' }}>{mode === 'login' ? 'Sign in' : 'Sign up'}</ThemedText>}
      </Pressable>
      <Link href={mode === 'login' ? '/sign-up' : '/login'}>
        <ThemedText themeColor="accent">{mode === 'login' ? 'No account? Sign up' : 'Have an account? Sign in'}</ThemedText>
      </Link>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', padding: Spacing.four, gap: Spacing.three },
  input: { borderRadius: 10, padding: Spacing.three, fontSize: 16 },
  button: { borderRadius: 10, padding: Spacing.three, alignItems: 'center' },
});
