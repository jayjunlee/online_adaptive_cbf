import pandas as pd
import tensorflow as tf
import numpy as np
import evidential_deep_learning as edl
from scipy.stats import invgamma, norm
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.mixture import GaussianMixture
import matplotlib.pyplot as plt
import seaborn as sns
import joblib

class EvidentialDeepRegression:
    def __init__(self, epochs=1000, batch_size=32, learning_rate=1e-6):
        self.epochs = epochs
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.scaler = StandardScaler()
        self.model = None
        self.history = None

    def load_and_preprocess_data(self, data_file):
        # Load the CSV file
        self.data_file = data_file
        df = pd.read_csv(self.data_file)

        # Extract inputs and outputs
        X = df[['Distance', 'Velocity', 'Theta', 'Gamma1', 'Gamma2']].values
        y_safety_loss = df['Safety Loss'].values.reshape(-1, 1)
        y_deadlock_time = df['Deadlock Time'].values.reshape(-1, 1)

        # Transform Theta into sine and cosine components
        Theta = X[:, 2]
        # angle wrapping, refers to this paper: "Learning with 3D rotations, a hitchhiker's guide to SO(3)", https://arxiv.org/abs/2404.11735
        X_sin_cos = np.column_stack(
            (X[:, :2], np.sin(Theta), np.cos(Theta), X[:, 3:]))

        # Normalize the inputs
        X_scaled = self.scaler.fit_transform(X_sin_cos)
        
        # Save the scaler
        joblib.dump(self.scaler, 'scaler.save')        

        return X_scaled, y_safety_loss, y_deadlock_time

    def EvidentialRegressionLoss(self, true, pred):
        '''Custom loss function to handle the custom regularizer coefficient'''
        return edl.losses.EvidentialRegression(true, pred, coeff=1e-2)

    def build_and_compile_model(self, input_shape):
        '''Define the evidential deep learning model for both outputs'''
        input_layer = tf.keras.layers.Input(shape=(input_shape,))
        dense_1 = tf.keras.layers.Dense(128, activation="relu")(input_layer)
        dense_2 = tf.keras.layers.Dense(128, activation="relu")(dense_1)
        dropout_1 = tf.keras.layers.Dropout(0.2)(dense_2)
        dense_3 = tf.keras.layers.Dense(64, activation="relu")(dropout_1)
        dropout_2 = tf.keras.layers.Dropout(0.2)(dense_3)
        dense_4 = tf.keras.layers.Dense(64, activation="relu")(dropout_2)

        # Output layer for safety loss
        output_safety_loss = edl.layers.DenseNormalGamma(1)(dense_4)

        # Output layer for deadlock time
        output_deadlock_time = edl.layers.DenseNormalGamma(1)(dense_4)

        # Define the model with two outputs
        self.model = tf.keras.models.Model(inputs=input_layer, outputs=[
                                           output_safety_loss, output_deadlock_time])

        # Compile the model with multiple losses
        self.model.compile(
            optimizer=tf.keras.optimizers.Adam(self.learning_rate),
            loss={
                'dense_normal_gamma': self.EvidentialRegressionLoss,
                'dense_normal_gamma_1': self.EvidentialRegressionLoss
            }
        )

    def train_model(self, X_scaled, y_safety_loss, y_deadlock_time):
        early_stopping = tf.keras.callbacks.EarlyStopping(
            monitor='val_loss',
            patience=20,
            restore_best_weights=True
        )

        reduce_lr_on_plateau = tf.keras.callbacks.ReduceLROnPlateau(
            monitor='val_loss',
            factor=0.2,
            patience=5,
            min_lr=1e-8
        )

        model_checkpoint = tf.keras.callbacks.ModelCheckpoint(
            filepath='edr_model_best.h5',
            monitor='val_loss',
            save_best_only=True
        )

        self.history = self.model.fit(
            X_scaled, [y_safety_loss, y_deadlock_time],
            epochs=self.epochs,
            batch_size=self.batch_size,
            validation_split=0.2,
            callbacks=[early_stopping, reduce_lr_on_plateau, model_checkpoint]
        )

    def save_model(self, model_name):
        self.model.save(model_name)

    def load_saved_model(self, model_name):
        self.model = tf.keras.models.load_model(
            model_name,
            custom_objects={
                'DenseNormalGamma': edl.layers.DenseNormalGamma,
                'EvidentialRegression': EvidentialDeepRegression.EvidentialRegressionLoss
            }
        )

    def load_saved_scaler(self, scaler_path):
        self.scaler = joblib.load(scaler_path)

    def calculate_uncertainties(self, y_pred):
        '''Calculate aleatoric and epistemic uncertainties from model predictions'''
        gamma, v, alpha, beta = tf.split(y_pred, 4, axis=-1)
        gamma = gamma.numpy()[:, 0]
        v = v.numpy()[:, 0]
        alpha = alpha.numpy()[:, 0]
        beta = beta.numpy()[:, 0]
        aleatoric_uncertainty = beta / (alpha - 1)
        epistemic_uncertainty = beta / (v * (alpha - 1))
        return gamma, aleatoric_uncertainty, epistemic_uncertainty

    def get_gaussian_distributions(self, gamma, v, alpha, beta):
        '''Get Gaussian distribution for the mean and Inverse-Gamma distribution for the variance.'''
        gaussians = [norm(loc=g, scale=np.sqrt(b / v))
                     for g, v, b in zip(gamma, v, beta)]
        inv_gammas = [invgamma(a=a, scale=b) for a, b in zip(alpha, beta)]

        return gaussians, inv_gammas

    def create_gmm(self, y_pred, num_samples=3):
        '''Sample 3 pairs of (mean, variance) and create a Gaussian Mixture Model (GMM)'''
        gamma, v, alpha, beta = tf.split(y_pred, 4, axis=-1)
        gamma = gamma.numpy()
        v = v.numpy()
        alpha = alpha.numpy()
        beta = beta.numpy()

        gaussians, inv_gammas = self.get_gaussian_distributions(
            gamma, v, alpha, beta)
        means = []
        variances = []

        for _ in range(num_samples):
            for gaussian, inv_gamma in zip(gaussians, inv_gammas):
                variance = inv_gamma.rvs()
                variances.append(variance)
                mean = gaussian.rvs()
                means.append(mean)

        gmm = GaussianMixture(n_components=num_samples)
        gmm.means_ = np.array(means).reshape(-1, 1)
        gmm.covariances_ = np.array(variances).reshape(-1, 1, 1)
        gmm.weights_ = np.ones(num_samples) / num_samples
        gmm.precisions_cholesky_ = np.array([np.linalg.cholesky(np.linalg.inv(
            cov)) for cov in gmm.covariances_])  # For efficient computation

        return gmm



def plot_training_history(history):
    plt.figure(figsize=(12, 6))

    # Safety Loss Model Training History
    plt.subplot(1, 2, 1)
    plt.plot(history.history['dense_normal_gamma_loss'],
             label='Training Loss - Safety Loss')
    plt.plot(history.history['val_dense_normal_gamma_loss'],
             label='Validation Loss - Safety Loss')
    plt.title('Safety Loss Model Training History')
    plt.xlabel('Epoch')
    plt.ylabel('Loss')
    plt.legend()

    # Deadlock Time Model Training History
    plt.subplot(1, 2, 2)
    plt.plot(history.history['dense_normal_gamma_1_loss'],
             label='Training Loss - Deadlock Time')
    plt.plot(history.history['val_dense_normal_gamma_1_loss'],
             label='Validation Loss - Deadlock Time')
    plt.title('Deadlock Time Model Training History')
    plt.xlabel('Epoch')
    plt.ylabel('Loss')
    plt.legend()

    plt.show()

def evaluate_predictions(y_true, y_pred, name):
    gamma, v, alpha, beta = tf.split(y_pred, 4, axis=-1)
    gamma = gamma.numpy()[:, 0]

    mse = mean_squared_error(y_true, gamma)
    rmse = np.sqrt(mse)
    mae = mean_absolute_error(y_true, gamma)

    print(f"Evaluation metrics for {name}:")
    print(f"MSE: {mse}")
    print(f"RMSE: {rmse}")
    print(f"MAE: {mae}")
    print()

def plot_gmm(gmm):
    x = np.linspace(gmm.means_.min() - 3, gmm.means_.max() +
                    3, 1000).reshape(-1, 1)
    logprob = gmm.score_samples(x)
    responsibilities = gmm.predict_proba(x)
    pdf = np.exp(logprob)
    pdf_individual = responsibilities * pdf[:, np.newaxis]

    plt.figure(figsize=(10, 6))
    plt.plot(x, pdf, '-k', label='GMM')

    for i in range(pdf_individual.shape[1]):
        plt.plot(x, pdf_individual[:, i], '--', label=f'GMM Component {i+1}')

    plt.xlabel('Safety Loss Prediction')
    plt.ylabel('Density')
    plt.title('Gaussian Mixture Model for Safety Loss Predictions')
    plt.legend()
    plt.show()


if __name__ == "__main__":
    Test = False # Set to True if you want to test the model without training
    model_name = 'edr_model_0720.h5'
    scaler_name = 'scaler.save'    
    data_file = 'data_generation_results_9datapoint.csv'

    batch_size = 128
    edr = EvidentialDeepRegression(batch_size=batch_size, learning_rate=2e-6)
    X_scaled, y_safety_loss, y_deadlock_time = edr.load_and_preprocess_data(data_file)

    # Registering the custom loss function in TensorFlow's serialization framework:
    tf.keras.utils.get_custom_objects().update({
        'EvidentialRegressionLoss': EvidentialDeepRegression.EvidentialRegressionLoss
    })

    if Test:
        edr.load_saved_model(model_name)
        edr.load_saved_scaler(scaler_name)
    else:
        edr.build_and_compile_model(X_scaled.shape[1])
        edr.train_model(X_scaled, y_safety_loss, y_deadlock_time)
        edr.save_model(model_name)
        plot_training_history(edr.history)

    # Predict and plot using the trained model
    y_pred_safety_loss, y_pred_deadlock_time = edr.model.predict(X_scaled)

    # Evaluate the predictions
    evaluate_predictions(y_safety_loss, y_pred_safety_loss, "Safety Loss")

    # Create GMM for safety loss predictions
    gmm_safety = edr.create_gmm(y_pred_safety_loss[0])
    plot_gmm(gmm_safety)