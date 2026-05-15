import styles from './Login.module.css'

export default function Login() {
  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <div className={styles.logo}>⬡ Kalshi Analytics</div>
        <p className={styles.subtitle}>Sign in to continue</p>
        <a href="/api/auth/login" className={styles.googleBtn}>
          Sign in with Google
        </a>
      </div>
    </div>
  )
}
