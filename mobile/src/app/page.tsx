import { redirect } from 'next/navigation';
import { getCurrentUser } from '@/lib/session';

export default function HomePage() {
  const user = getCurrentUser();
  redirect(user ? '/dashboard' : '/login');
}
